//! Memory: what happens at the desk over weeks and months, and the round report after a game
//! (services/memory.py).
//!
//! Once a second the service takes a sample of the readings and keeps, in a database on this
//! computer (`memory_store`): a row per minute (means and highest values, how long music
//! played, never what), a row per game played (frame rates, what slowed it, the highest
//! temperatures), the cooling state whenever it moves (temperatures, fan speeds and duty,
//! power, load, the temperature each FanControl curve reads) and every change to FanControl's
//! curves. Titles of tracks, windows and files are never read, so none can be kept; of a game
//! only the name and the program's name are.
//!
//! When a game has been played long enough, the service builds a round report: the round
//! against the last ones of the same program, what all its frames came to, and how warm the
//! CPU and the GPU were over it. It publishes it (`report` and `report.*`) and shows a theme
//! for a while, which a new game ends at once. The game itself comes from the `game` service
//! (`game.name`) and the frame rates from PresentMon or MangoHud (`game.*`).

use std::collections::HashSet;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use indexmap::IndexMap;
use lp_py::{Dict, Value};
use lp_render::snapshot::{Reading, Snapshot};

use crate::fancontrol;
use crate::host::{Event, Scope, ServiceHost, Transition};
use crate::manager::{Part, Schema, Service};
use crate::memory_store::{
    last_fingerprint, Store, COOLING_VALUES, DATABASE, FRAME_FIGURES, MEANS, PEAKS, SESSION_PEAKS,
};
use crate::notices::is_playing;
use crate::paths::{self, Path, HERE};
use crate::round_report::{self, Totals};
use crate::steam::Places;

/// one sample a second, however often the main loop takes readings
const SAMPLE_S: f64 = 1.0;
/// after a pause longer than this the grid starts afresh instead of catching up
const GAP_S: f64 = 5.0;
/// a game in progress is written this often
const SESSION_WRITE_S: f64 = 60.0;
/// the names of the sensors are looked at this often
const SENSORS_EVERY_S: f64 = 60.0;
/// the readings of a report outlive it: its theme leaves with a transition
const REPORT_GRACE_S: f64 = 3.0;
const DAY_S: f64 = 86400.0;
// The cooling state is written when something moves, not on a clock (it all stands still at
// the desk): at least COOLING_MIN_S apart (less in a game, where it changes fast) and at
// least every COOLING_MAX_S, so a larger gap always means the service did not run.
const COOLING_MIN_S: f64 = 12.0;
const COOLING_MIN_GAME_S: f64 = 5.0;
const COOLING_MAX_S: f64 = 60.0;
/// The course of a round's temperatures has COURSE_POINTS to twice as many points, each the
/// mean of as many seconds: two and two become one when there are twice as many (`Course`).
const COURSE_POINTS: usize = 60;
const COURSE: [&str; 2] = ["cpu_temp", "gpu_temp"];
// How far a value must move to count; below that it is noise.
/// K
const MOVE_TEMP: f64 = 2.0;
/// a fan's speed jitters by about 25
const MOVE_RPM: f64 = 40.0;
/// percentage points: what the fan is told, the thing that matters
const MOVE_DUTY: f64 = 1.0;
/// W
const MOVE_POWER: f64 = 15.0;
/// percent
const MOVE_LOAD: f64 = 20.0;
const DEFAULT_LEVELS: [f64; 5] = [60.0, 90.0, 120.0, 144.0, 165.0];
/// Where each number comes from. Core keys only: whatever source supplies them.
const SOURCES: [(&str, &str); 14] = [
    ("cpu_load", "cpu.load"),
    ("gpu_load", "gpu.load"),
    ("cpu_temp", "cpu.temp"),
    ("gpu_temp", "gpu.temp"),
    ("cpu_power", "cpu.power"),
    ("gpu_power", "gpu.power"),
    ("cpu_clock", "cpu.freq"),
    ("gpu_clock", "gpu.freq"),
    ("mem_used", "mem.used"),
    ("vram_used", "gpu.mem.used"),
    ("net_down", "net.down"),
    ("net_up", "net.up"),
    ("volume", "audio.volume"),
    ("power", "power.watts"),
];
/// What the service asks the sources for, so that those that work on demand run for it.
const WANTED: [&str; 11] = [
    "cpu.load",
    "gpu.load",
    "cpu.temp",
    "gpu.temp",
    "cpu.power",
    "gpu.power",
    "cpu.freq",
    "gpu.freq",
    "gpu.mem.used",
    "fan.*",
    "game.*",
];
/// the mode of a game (modes.GAME)
const GAME: &str = "game";

/// number: a reading's value as a finite number, or none (services/readings.py).
fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Int(_) => value.to_f64().filter(|found| found.is_finite()),
        Value::Float(found) if found.is_finite() => Some(*found),
        _ => None,
    }
}

/// The service's options, checked.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub keep_days: f64,
    pub cooling: bool,
    pub cooling_days: f64,
    pub music: bool,
    pub report: bool,
    pub report_min_s: f64,
    pub report_compare: i64,
    pub report_seconds: f64,
    pub report_theme: String,
    pub report_transition: String,
    pub fps_levels: Vec<f64>,
    /// the levels are the usual ones, which Python has as whole numbers (none were given)
    pub usual_levels: bool,
    pub temperatures: Vec<String>,
}

impl Settings {
    /// from_options: the settings of a service's options; what is wrong with them is
    /// reported.
    pub fn from_options(options: &Dict, report: &mut dyn FnMut(String)) -> Settings {
        let list = |key: &str| options.get(key).and_then(Value::as_list).unwrap_or(&[]);
        let float = |key: &str| options.get(key).and_then(Value::to_f64).unwrap_or(0.0);
        let on = |key: &str| options.get(key).is_some_and(Value::truthy);
        let text = |key: &str| options.get(key).map(Value::str).unwrap_or_default();
        let given = list("fps_levels");
        let positive = |n: &Value| number(n).filter(|n| *n > 0.0);
        let mut levels: Vec<f64> = given.iter().filter_map(positive).collect();
        levels.sort_by(f64::total_cmp);
        levels.dedup();
        if levels.len() != given.len() {
            report("fps_levels: only positive numbers count, once each".into());
        }
        let named = list("temperatures");
        let temperatures: Vec<String> = (named.iter().filter_map(Value::as_str))
            .map(|key| lp_py::strip(key).to_string())
            .filter(|key| !key.is_empty())
            .collect();
        if temperatures.len() != named.len() {
            let wrong = "temperatures: expected readings such as 'temp.board.system' or \
                         'temp.nvme.*'";
            report(wrong.into());
        }
        let compare = match options.get("report_compare") {
            Some(Value::Int(n)) => i64::try_from(n).unwrap_or(i64::MAX),
            _ => 1,
        };
        let at_least = |least: f64, given: f64| if given > least { given } else { least };
        Settings {
            keep_days: float("keep_days"),
            cooling: on("cooling"),
            cooling_days: float("cooling_days"),
            music: on("music"),
            report: on("report"),
            report_min_s: at_least(0.0, float("report_minutes")) * 60.0,
            report_compare: compare.max(1),
            report_seconds: at_least(1.0, float("report_seconds")),
            report_theme: text("report_theme"),
            report_transition: text("report_transition"),
            usual_levels: levels.is_empty(),
            fps_levels: match levels.is_empty() {
                true => DEFAULT_LEVELS.to_vec(),
                false => levels,
            },
            temperatures,
        }
    }
}

// -- one sample ----------------------------------------------------------------------------------

/// A fan's speed and duty (percent).
type FanState = (Option<f64>, Option<f64>);

/// What one second of the readings says. `fans` map a name to (rpm, duty percent).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Sample {
    /// Unix time
    pub at: f64,
    /// by column of MEANS (and `power`)
    pub values: IndexMap<&'static str, Option<f64>>,
    pub game: Option<String>,
    pub program: String,
    pub fps: Option<f64>,
    pub low: Option<f64>,
    pub frametime: Option<f64>,
    /// what the round's frames came to
    pub frames: IndexMap<&'static str, f64>,
    pub limiter: Option<String>,
    pub music: bool,
    pub outside: Option<f64>,
    pub fans: IndexMap<String, FanState>,
    /// more temperatures, by reading key
    pub temps: IndexMap<String, f64>,
    /// what each curve reads, by curve
    pub sources: IndexMap<String, f64>,
}

/// gigabytes: a memory size in GB: LibreHardwareMonitor gives the graphics memory in MB.
fn gigabytes(reading: &Reading) -> Option<f64> {
    let factor = match reading.unit.as_str() {
        "" | "GB" | "GiB" => 1.0,
        "MB" | "MiB" => 1.0 / 1024.0,
        "B" => 2.0f64.powi(-30),
        _ => return None,
    };
    Some(number(&reading.value)? * factor)
}

fn positive(value: &Value) -> Option<f64> {
    number(value).filter(|found| *found > 0.0)
}

type Readings = IndexMap<String, Reading>;

/// Takes the numbers the memory keeps out of a snapshot. Knows the names FanControl gave the
/// fans and the sensor each curve reads, once the service has read its configuration.
pub struct Reader {
    settings: Settings,
    /// fan sensor id -> the name in FanControl
    pub names: IndexMap<String, String>,
    /// fans FanControl hides: connectors with nothing on them
    pub hidden: Vec<String>,
    /// curve -> id of the sensor it reads
    pub sources: IndexMap<String, String>,
    hotspot: Option<String>,
}

impl Reader {
    pub fn new(settings: &Settings) -> Reader {
        Reader {
            settings: settings.clone(),
            names: IndexMap::new(),
            hidden: Vec::new(),
            sources: IndexMap::new(),
            hotspot: None,
        }
    }

    pub fn read(&mut self, snapshot: &Snapshot, at: f64) -> Sample {
        let readings = &snapshot.readings;
        let mut values = IndexMap::new();
        for (column, key) in SOURCES {
            let value = readings.get(key).and_then(|reading| match column {
                "vram_used" => gigabytes(reading),
                _ => number(&reading.value),
            });
            values.insert(column, value);
        }
        values.insert("gpu_hotspot", self.gpu_hotspot(readings));
        let name = match snapshot.value("game.name") {
            Value::Str(name) if !name.is_empty() => Some(name),
            _ => None,
        };
        let mut sample = Sample {
            at,
            values,
            game: name,
            ..Sample::default()
        };
        if sample.game.is_some() {
            Self::game(snapshot, &mut sample);
        }
        sample.music = self.settings.music && is_playing(snapshot);
        sample.outside = Self::outside(readings.get("weather.temperature"));
        sample.fans = self.fans(readings);
        let glob = |entry: &str| entry.contains(['*', '?', '[']);
        for entry in &self.settings.temperatures {
            let keys: Vec<&str> = match glob(entry) {
                true => lp_py::fnmatch::filter(readings.keys().map(String::as_str), entry)
                    .unwrap_or_default(),
                false => vec![entry.as_str()],
            };
            for key in keys {
                if let Some(value) = readings.get(key).and_then(|r| number(&r.value)) {
                    sample.temps.insert(key.to_string(), value);
                }
            }
        }
        for (curve, sensor) in &self.sources {
            let reading = readings.get(&format!("lhm:{sensor}"));
            if let Some(value) = reading.and_then(|reading| number(&reading.value)) {
                sample.sources.insert(curve.clone(), value);
            }
        }
        sample
    }

    fn game(snapshot: &Snapshot, sample: &mut Sample) {
        sample.fps = positive(&snapshot.value("game.fps"));
        sample.low = positive(&snapshot.value("game.low"));
        sample.frametime = positive(&snapshot.value("game.frametime"));
        for name in FRAME_FIGURES {
            if let Some(value) = positive(&snapshot.value(&format!("game.{name}"))) {
                sample.frames.insert(name, value);
            }
        }
        sample.limiter = match snapshot.value("game.limiter") {
            Value::Str(limiter) if ["cpu", "gpu", "cap"].contains(&limiter.as_str()) => {
                Some(limiter)
            }
            _ => None,
        };
        if let Value::Str(program) = snapshot.value("game.app") {
            let program = lp_py::lower(lp_py::strip(&program));
            sample.program = match program.strip_suffix(".exe") {
                Some(bare) => bare.to_string(),
                None => program,
            };
        }
    }

    fn outside(reading: Option<&Reading>) -> Option<f64> {
        let reading = reading?;
        let value = number(&reading.value)?;
        Some(match reading.unit.as_str() {
            "°F" => (value - 32.0) * 5.0 / 9.0,
            _ => value,
        })
    }

    /// The graphics card's hot spot: the one of the card `gpu.temp` is read from, found by
    /// its name once and kept as long as it is there.
    fn gpu_hotspot(&mut self, readings: &Readings) -> Option<f64> {
        let known = |key: &Option<String>| key.as_ref().and_then(|key| readings.get(key));
        if known(&self.hotspot).is_none() {
            self.hotspot = Self::find_hotspot(readings);
        }
        number(&known(&self.hotspot)?.value)
    }

    fn find_hotspot(readings: &Readings) -> Option<String> {
        let card = readings.get("gpu.temp").map_or("", |main| {
            main.origin
                .rfind("/temperature/")
                .map_or("", |at| &main.origin[..at])
        });
        let mut found: Vec<(bool, &String)> = Vec::new();
        for (key, reading) in readings {
            if !key.starts_with("temp.gpu") {
                continue;
            }
            let words = lp_py::lower(&format!("{key} {}", reading.label)).replace('_', " ");
            if ["hot spot", "hotspot", "junction"]
                .iter()
                .any(|word| words.contains(word))
            {
                let main = !card.is_empty() && reading.origin.starts_with(&format!("{card}/"));
                found.push((!main, key));
            }
        }
        found.into_iter().min().map(|(_, key)| key.clone()) // the main card's first
    }

    /// sensors: the names of the hardware and of every temperature sensor, for the cooling
    /// analysis to speak of them by name: `cpu` and `gpu` as the readings call them, and for
    /// each `temp.*` reading its id (where LibreHardwareMonitor gave one), its label, and
    /// `panel`: whether it is the sensor behind `cpu_temp`, `gpu_temp` or `gpu_hotspot`.
    pub fn sensors(&self, readings: &Readings) -> Dict {
        let mut behind: IndexMap<&str, &str> = IndexMap::new();
        let known = self
            .hotspot
            .clone()
            .filter(|key| readings.contains_key(key));
        let hotspot = known.or_else(|| Self::find_hotspot(readings));
        let columns = [
            ("cpu_temp", Some("cpu.temp".to_string())),
            ("gpu_temp", Some("gpu.temp".to_string())),
            ("gpu_hotspot", hotspot),
        ];
        for (column, key) in &columns {
            let reading = key.as_ref().and_then(|key| readings.get(key));
            if let Some(reading) = reading.filter(|reading| !reading.origin.is_empty()) {
                behind.insert(&reading.origin, column);
            }
        }
        let mut out = Dict::default();
        for kind in ["cpu", "gpu"] {
            let name = match readings.get(&format!("{kind}.name")).map(|r| &r.value) {
                Some(name @ Value::Str(_)) => name.clone(),
                _ => Value::None,
            };
            out.insert(kind.to_string(), name);
        }
        let mut keys: Vec<&String> = readings.keys().collect();
        keys.sort();
        let mut found = Vec::new();
        for key in keys {
            let reading = &readings[key];
            if key.starts_with("temp.") && number(&reading.value).is_some() {
                let ident = reading.origin.strip_prefix("lhm:");
                let panel = behind.get(reading.origin.as_str());
                let text = |text: Option<&str>| text.map_or(Value::None, |t| Value::Str(t.into()));
                let mut sensor = Dict::default();
                sensor.insert("key".into(), Value::Str(key.clone()));
                sensor.insert("id".into(), text(ident));
                sensor.insert("label".into(), Value::Str(reading.label.clone()));
                sensor.insert("panel".into(), text(panel.copied()));
                found.push(Value::Dict(sensor));
            }
        }
        out.insert("sensors".into(), Value::List(found));
        out
    }

    /// Every fan: its speed and, where LibreHardwareMonitor also has the output that drives
    /// it, the duty. Named as FanControl names it, else as the sensor does.
    fn fans(&self, readings: &Readings) -> IndexMap<String, FanState> {
        let mut fans = IndexMap::new();
        for (key, reading) in readings {
            let Some(speed) = number(&reading.value).filter(|_| key.starts_with("fan.")) else {
                continue;
            };
            let sensor = reading.origin.strip_prefix("lhm:").unwrap_or("");
            if self.hidden.iter().any(|hidden| hidden == sensor) {
                continue;
            }
            let duty = match sensor.is_empty() {
                true => None,
                false => readings
                    .get(&format!("lhm:{}", sensor.replace("/fan/", "/control/")))
                    .and_then(|output| number(&output.value)),
            };
            let given = self.names.get(sensor).filter(|name| !name.is_empty());
            let label = Some(&reading.label).filter(|label| !label.is_empty());
            let mut name = given.or(label).unwrap_or(key).clone();
            if fans.contains_key(&name) {
                name.clone_from(key); // two fans with one name
            }
            fans.insert(name, (Some(speed), duty));
        }
        fans
    }
}

// -- turning samples into rows -------------------------------------------------------------------

fn minute_columns() -> Vec<String> {
    let mut columns: Vec<String> = ["ts", "samples", "mode", "game"].map(String::from).to_vec();
    columns.extend(MEANS.map(String::from));
    columns.extend(PEAKS.iter().map(|name| format!("{name}_max")));
    columns.extend(["fps", "fps_low", "music_s", "outside_temp", "fans"].map(String::from));
    columns
}

fn session_columns() -> Vec<String> {
    let first = [
        "id",
        "game",
        "program",
        "started",
        "ended",
        "duration_s",
        "fps_s",
        "fps_mean",
        "fps_p01",
        "fps_low_mean",
        "frametime_mean",
        "levels",
        "limit_cpu_s",
        "limit_gpu_s",
        "limit_cap_s",
    ];
    let mut columns: Vec<String> = first.map(String::from).to_vec();
    columns.extend(SESSION_PEAKS.iter().map(|name| format!("{name}_max")));
    columns.extend(["outside_temp", "closed"].map(String::from));
    columns.extend(FRAME_FIGURES.map(String::from));
    columns.push("course".into());
    columns
}

fn cooling_columns() -> Vec<String> {
    let mut columns: Vec<String> = ["ts", "mode", "game"].map(String::from).to_vec();
    columns.extend(COOLING_VALUES.map(String::from));
    columns.extend(["outside_temp", "temps", "fans", "sources"].map(String::from));
    columns
}

/// insert: the statement that writes a row of these columns.
fn insert(table: &str, columns: &[String]) -> String {
    let marks = vec!["?"; columns.len()].join(", ");
    format!(
        "INSERT OR REPLACE INTO {table} ({}) VALUES ({marks})",
        columns.join(", ")
    )
}

fn float(value: Option<f64>) -> Value {
    value.map_or(Value::None, Value::Float)
}

fn text(value: Option<&String>) -> Value {
    value.map_or(Value::None, |text| Value::Str(text.clone()))
}

fn mean(values: &[f64]) -> Value {
    match values.len() {
        0 => Value::None,
        count => Value::Float(lp_py::sum_floats(values.iter().copied()) / count as f64),
    }
}

/// `round(x)` as a whole number.
fn whole(x: f64) -> Value {
    Value::Int(
        format!("{:.0}", lp_py::round(x))
            .parse()
            .unwrap_or_default(),
    )
}

/// `int(x)` as a whole number.
fn cut(x: f64) -> Value {
    Value::Int(format!("{:.0}", x.trunc()).parse().unwrap_or_default())
}

fn dumped(value: Dict) -> Value {
    let how = lp_py::json::Dumps::default();
    Value::Str(lp_py::json::dumps(&Value::Dict(value), &how))
}

/// The samples of one minute.
struct Minute {
    ts: f64,
    n: i64,
    total: IndexMap<&'static str, f64>,
    count: IndexMap<&'static str, u64>,
    peak: IndexMap<&'static str, f64>,
    fan_total: IndexMap<String, f64>,
    fan_count: IndexMap<String, u64>,
    music_s: i64,
    fps: Vec<f64>,
    low: Vec<f64>,
    game: Option<String>,
    mode: &'static str,
    outside: Option<f64>,
}

impl Minute {
    fn new(ts: f64) -> Minute {
        Minute {
            ts,
            n: 0,
            total: IndexMap::new(),
            count: IndexMap::new(),
            peak: IndexMap::new(),
            fan_total: IndexMap::new(),
            fan_count: IndexMap::new(),
            music_s: 0,
            fps: Vec::new(),
            low: Vec::new(),
            game: None,
            mode: "normal",
            outside: None,
        }
    }

    fn add(&mut self, sample: &Sample) {
        self.n += 1;
        for (key, value) in &sample.values {
            let Some(value) = value else {
                continue;
            };
            *self.total.entry(key).or_insert(0.0) += value;
            *self.count.entry(key).or_insert(0) += 1;
            let highest = self.peak.get(key).copied().unwrap_or(f64::NEG_INFINITY);
            if PEAKS.contains(key) && *value > highest {
                self.peak.insert(key, *value);
            }
        }
        for (name, (rpm, _duty)) in &sample.fans {
            if let Some(rpm) = rpm {
                *self.fan_total.entry(name.clone()).or_insert(0.0) += rpm;
                *self.fan_count.entry(name.clone()).or_insert(0) += 1;
            }
        }
        self.music_s += i64::from(sample.music);
        if let Some(game) = &sample.game {
            (self.mode, self.game) = ("game", Some(game.clone()));
            if let Some(fps) = sample.fps {
                self.fps.push(fps);
                self.low.extend(sample.low);
            }
        }
        if sample.outside.is_some() {
            self.outside = sample.outside;
        }
    }

    fn row(&self) -> Vec<Value> {
        let fans: Dict = (self.fan_count.iter())
            .map(|(name, count)| (name.clone(), whole(self.fan_total[name] / *count as f64)))
            .collect();
        let mut row = vec![
            cut(self.ts),
            Value::int(self.n),
            Value::Str(self.mode.into()),
            text(self.game.as_ref()),
        ];
        row.extend(MEANS.iter().map(|key| match self.count.get(key) {
            Some(count) if *count > 0 => Value::Float(self.total[key] / *count as f64),
            _ => Value::None,
        }));
        row.extend(PEAKS.iter().map(|key| float(self.peak.get(key).copied())));
        row.extend([
            mean(&self.fps),
            mean(&self.low),
            Value::int(self.music_s),
            float(self.outside),
            match fans.is_empty() {
                true => Value::None,
                false => dumped(fans),
            },
        ]);
        row
    }
}

/// How some values went over a round, as `COURSE_POINTS` to twice as many points, each the
/// mean of the same number of samples: when there are twice as many, two and two become one.
/// A round of any length keeps the same few numbers, and the whole round is in them.
struct Course {
    /// samples to a point
    width: u64,
    /// sums and counts, by name
    points: Vec<([f64; 2], [u64; 2])>,
    /// samples in the last point
    filled: u64,
}

impl Course {
    fn new() -> Course {
        Course {
            width: 1,
            points: Vec::new(),
            filled: 0,
        }
    }

    fn add(&mut self, values: &IndexMap<&'static str, Option<f64>>) {
        if self.points.is_empty() || self.filled >= self.width {
            if self.points.len() == 2 * COURSE_POINTS {
                self.halve();
            }
            self.points.push(([0.0; 2], [0; 2]));
            self.filled = 0;
        }
        self.filled += 1;
        let Some((sums, counts)) = self.points.last_mut() else {
            return;
        };
        for (at, name) in COURSE.iter().enumerate() {
            if let Some(value) = values.get(name).copied().flatten() {
                sums[at] += value;
                counts[at] += 1;
            }
        }
    }

    fn halve(&mut self) {
        let pairs = self.points.chunks_exact(2);
        let both = pairs.map(|pair| {
            let ((s1, c1), (s2, c2)) = (pair[0], pair[1]);
            (
                [s1[0] + s2[0], s1[1] + s2[1]],
                [c1[0] + c2[0], c1[1] + c2[1]],
            )
        });
        self.points = both.collect();
        self.width *= 2;
    }

    /// `{"seconds": per point, name: [mean or None, ...]}`; None while nothing told a value.
    fn course(&self) -> Option<Dict> {
        if !self.points.iter().any(|(_, counts)| counts != &[0, 0]) {
            return None;
        }
        let mut found = Dict::default();
        found.insert("seconds".into(), Value::Float(self.width as f64 * SAMPLE_S));
        for (at, name) in COURSE.iter().enumerate() {
            let means = self.points.iter().map(|(sums, counts)| match counts[at] {
                0 => Value::None,
                count => Value::Float(lp_py::round_digits(sums[at] / count as f64, 1)),
            });
            found.insert((*name).to_string(), Value::List(means.collect()));
        }
        Some(found)
    }
}

/// A game being played, from its first sample to its last.
pub struct Session {
    id: i64,
    game: Option<String>,
    program: String,
    started: f64,
    written: f64,
    edges: Vec<f64>,
    /// the edges are whole numbers in what is kept
    whole_edges: bool,
    fps: Vec<f64>,
    /// the sum of `fps`, for its mean
    total: f64,
    low: Vec<f64>,
    frametime: Vec<f64>,
    levels: Vec<u64>,
    limits: [i64; 3],
    peaks: IndexMap<&'static str, f64>,
    outside: Option<f64>,
    /// what the frames came to, by program: (seconds it told them, the last it told); the
    /// round's are those of the program that told them longest (the game, not its launcher)
    frames: IndexMap<String, (u64, IndexMap<&'static str, f64>)>,
    course: Course,
}

impl Session {
    fn new(sample: &Sample, edges: &[f64], whole_edges: bool) -> Session {
        Session {
            id: (sample.at * 1000.0).trunc() as i64,
            game: sample.game.clone(),
            program: sample.program.clone(),
            started: sample.at,
            written: sample.at,
            edges: edges.to_vec(),
            whole_edges,
            fps: Vec::new(),
            total: 0.0,
            low: Vec::new(),
            frametime: Vec::new(),
            levels: vec![0; edges.len() + 1],
            limits: [0; 3],
            peaks: IndexMap::new(),
            outside: sample.outside,
            frames: IndexMap::new(),
            course: Course::new(),
        }
    }

    fn add(&mut self, sample: &Sample) {
        if self.program.is_empty() && !sample.program.is_empty() {
            self.program.clone_from(&sample.program);
        }
        if !sample.frames.is_empty() {
            let told = self.frames.entry(sample.program.clone()).or_default();
            *told = (told.0 + 1, sample.frames.clone());
        }
        self.course.add(&sample.values);
        if let Some(fps) = sample.fps {
            self.fps.push(fps);
            self.total += fps;
            // (bisect_right: the edges not above the frame rate)
            self.levels[self.edges.partition_point(|edge| *edge <= fps)] += 1;
            self.low.extend(sample.low);
            self.frametime.extend(sample.frametime);
            let limiter = sample.limiter.as_deref();
            if let Some(at) = ["cpu", "gpu", "cap"]
                .iter()
                .position(|l| Some(*l) == limiter)
            {
                self.limits[at] += 1;
            }
        }
        for key in SESSION_PEAKS {
            let Some(value) = sample.values.get(key).copied().flatten() else {
                continue;
            };
            if value > self.peaks.get(key).copied().unwrap_or(f64::NEG_INFINITY) {
                self.peaks.insert(key, value);
            }
        }
        if self.outside.is_none() {
            self.outside = sample.outside;
        }
    }

    /// totals: what the round has come to at `at`, for `round_report::session_readings`.
    pub fn totals(&self, at: f64) -> Totals {
        let played = at - self.started;
        Totals {
            duration_s: if played > 0.0 { played } else { 0.0 },
            fps_mean: (!self.fps.is_empty()).then(|| self.total / self.fps.len() as f64),
            edges: self.edges.clone(),
            seconds: self.levels.clone(),
            peaks: self.peaks.clone(),
        }
    }

    fn levels(&self) -> Dict {
        let edges = self.edges.iter().map(|edge| match self.whole_edges {
            true => whole(*edge),
            false => Value::Float(*edge),
        });
        let seconds = (self.levels.iter()).map(|n| Value::int(i64::try_from(*n).unwrap_or(0)));
        let mut levels = Dict::default();
        levels.insert("edges".into(), Value::List(edges.collect()));
        levels.insert("seconds".into(), Value::List(seconds.collect()));
        levels
    }

    fn row(&self, at: f64, closed: bool) -> Vec<Value> {
        let mut ranked = self.fps.clone();
        ranked.sort_by(f64::total_cmp);
        let mut told: Option<&(u64, IndexMap<&'static str, f64>)> = None;
        for frames in self.frames.values() {
            if told.is_none_or(|most| frames.0 > most.0) {
                told = Some(frames);
            }
        }
        let frames = |key: &str| float(told.and_then(|(_, frames)| frames.get(key).copied()));
        let p01 = ranked.get((ranked.len() as f64 * 0.01).trunc() as usize);
        let mut row = vec![
            Value::int(self.id),
            text(self.game.as_ref()),
            Value::Str(self.program.clone()),
            whole(self.started),
            whole(at),
            whole(at - self.started),
            Value::int(i64::try_from(ranked.len()).unwrap_or(0)),
            mean(&ranked),
            float(p01.copied()),
            mean(&self.low),
            mean(&self.frametime),
            dumped(self.levels()),
        ];
        row.extend(self.limits.map(Value::int));
        row.extend(SESSION_PEAKS.map(|key| float(self.peaks.get(key).copied())));
        row.extend([float(self.outside), Value::int(i64::from(closed))]);
        row.extend(FRAME_FIGURES.map(frames));
        row.push(self.course.course().map_or(Value::None, dumped));
        row
    }
}

struct Cooling {
    at: f64,
    mode: &'static str,
    game: Option<String>,
    values: IndexMap<&'static str, Option<f64>>,
    fans: IndexMap<String, FanState>,
    temps: IndexMap<String, f64>,
    sources: IndexMap<String, f64>,
}

/// moved: whether the cooling state has changed by more than noise since the row written
/// last.
fn moved(last: &Cooling, now: &Cooling) -> bool {
    for (key, value) in &now.values {
        let before = last.values.get(key).copied().flatten();
        let (Some(value), Some(before)) = (value, before) else {
            if value.is_none() != before.is_none() {
                return true;
            }
            continue;
        };
        let step = if key.contains("temp") || key.contains("hotspot") {
            MOVE_TEMP
        } else if key.ends_with("power") {
            MOVE_POWER
        } else {
            MOVE_LOAD
        };
        if (value - before).abs() >= step {
            return true;
        }
    }
    let same = now.fans.len() == last.fans.len()
        && now.fans.keys().all(|name| last.fans.contains_key(name));
    if !same {
        return true;
    }
    let apart = |now: Option<f64>, before: Option<f64>, step: f64| match (now, before) {
        (Some(now), Some(before)) => (now - before).abs() >= step,
        (None, None) => false,
        _ => true,
    };
    for (name, (rpm, duty)) in &now.fans {
        let (before_rpm, before_duty) = last.fans[name];
        if apart(*rpm, before_rpm, MOVE_RPM) || apart(*duty, before_duty, MOVE_DUTY) {
            return true;
        }
    }
    let pools = [(&now.temps, &last.temps), (&now.sources, &last.sources)];
    pools.iter().any(|(pool, before)| {
        pool.iter()
            .any(|(k, v)| (v - before.get(k).copied().unwrap_or(-1e9)).abs() >= MOVE_TEMP)
    })
}

/// What a report that was built is handed to, with the stamp of the moment its game ended.
pub type OnReport = Arc<dyn Fn(Dict, u64) + Send + Sync>;
pub type Stamp = Arc<dyn Fn() -> u64 + Send + Sync>;

/// Turns samples, at most one a second, into rows for the store: the minutes, the games and
/// the cooling state. Time is whatever the samples say, so a test runs a day in a moment.
///
/// `on_report(report, stamp)` is called on the store's thread with the round report of a game
/// that has just ended (when it was long enough and had frame rates), and `stamp` as
/// `stamp()` was when the game ended: what the service uses to tell a report is out of date.
pub struct Recorder {
    store: Arc<Store>,
    settings: Settings,
    on_report: Option<OnReport>,
    stamp: Stamp,
    due: f64,
    minute: Option<Minute>,
    session: Option<Session>,
    cooling: Option<Cooling>,
    cleaned: f64,
}

impl Recorder {
    pub fn new(
        store: Arc<Store>,
        settings: &Settings,
        on_report: Option<OnReport>,
        stamp: Stamp,
    ) -> Recorder {
        Recorder {
            store,
            settings: settings.clone(),
            on_report,
            stamp,
            due: 0.0,
            minute: None,
            session: None,
            cooling: None,
            cleaned: 0.0,
        }
    }

    pub fn due(&self, at: f64) -> bool {
        at >= self.due
    }

    /// running: the game being played, if one is (read only).
    pub fn running(&self) -> Option<&Session> {
        self.session.as_ref()
    }

    pub fn add(&mut self, sample: &Sample) {
        if !self.due(sample.at) {
            return;
        }
        self.due = match sample.at - self.due < GAP_S {
            true => self.due + SAMPLE_S,
            false => sample.at + SAMPLE_S,
        };
        self.add_minute(sample);
        if self.settings.cooling {
            self.add_cooling(sample);
        }
        self.add_session(sample);
    }

    /// close: keep what is under way: the minute and the game (which counts as ended, but
    /// shows no report).
    pub fn close(&mut self, at: f64) {
        if self.minute.is_some() {
            self.finish_minute(at);
        }
        if self.session.is_some() {
            self.write_session(at, true, false);
            self.session = None;
        }
    }

    // -- minutes --------------------------------------------------------------------------------

    fn add_minute(&mut self, sample: &Sample) {
        let ts = ((sample.at / 60.0).floor() * 60.0).trunc();
        if self.minute.as_ref().is_some_and(|minute| minute.ts != ts) {
            self.finish_minute(sample.at);
        }
        self.minute
            .get_or_insert_with(|| Minute::new(ts))
            .add(sample);
    }

    fn finish_minute(&mut self, now: f64) {
        let Some(minute) = self.minute.take().filter(|minute| minute.n != 0) else {
            return;
        };
        self.store
            .put(&insert("minutes", &minute_columns()), minute.row());
        if now - self.cleaned > DAY_S {
            self.cleaned = now;
            self.clean(now);
        }
    }

    /// Forget what is older than the settings keep (zero: keep everything).
    fn clean(&self, now: f64) {
        if self.settings.keep_days > 0.0 {
            let cutoff = cut(now - self.settings.keep_days * DAY_S);
            self.store
                .put("DELETE FROM minutes WHERE ts < ?", vec![cutoff.clone()]);
            self.store
                .put("DELETE FROM sessions WHERE started < ?", vec![cutoff]);
        }
        if self.settings.cooling_days > 0.0 {
            let cutoff = cut(now - self.settings.cooling_days * DAY_S);
            self.store
                .put("DELETE FROM cooling WHERE ts < ?", vec![cutoff]);
        }
    }

    // -- cooling --------------------------------------------------------------------------------

    fn add_cooling(&mut self, sample: &Sample) {
        let mode = if sample.game.is_some() {
            "game"
        } else {
            "normal"
        };
        let values = COOLING_VALUES.map(|key| (key, sample.values.get(key).copied().flatten()));
        let now = Cooling {
            at: sample.at,
            mode,
            game: sample.game.clone(),
            values: values.into_iter().collect(),
            fans: sample.fans.clone(),
            temps: sample.temps.clone(),
            sources: sample.sources.clone(),
        };
        if let Some(last) = &self.cooling {
            if last.mode == mode && last.game == sample.game {
                let since = sample.at - last.at;
                let least = match mode {
                    "game" => COOLING_MIN_GAME_S,
                    _ => COOLING_MIN_S,
                };
                if since < least || (since < COOLING_MAX_S && !moved(last, &now)) {
                    return;
                }
            }
        }
        let pair = |(rpm, duty): &FanState| Value::List(vec![float(*rpm), float(*duty)]);
        let fans: Dict = (now.fans.iter())
            .map(|(name, fan)| (name.clone(), pair(fan)))
            .collect();
        let temps: Dict = (now.temps.iter())
            .map(|(key, value)| (key.clone(), Value::Float(*value)))
            .collect();
        let sources: Dict = (now.sources.iter())
            .map(|(k, v)| (k.clone(), Value::Float(lp_py::round_digits(*v, 1))))
            .collect();
        let json = |table: Dict| match table.is_empty() {
            true => Value::None,
            false => dumped(table),
        };
        let mut row = vec![
            cut(sample.at),
            Value::Str(mode.into()),
            text(sample.game.as_ref()),
        ];
        row.extend(now.values.values().map(|value| float(*value)));
        row.extend([
            float(sample.outside),
            json(temps),
            json(fans),
            json(sources),
        ]);
        self.cooling = Some(now);
        self.store.put(&insert("cooling", &cooling_columns()), row);
    }

    // -- games ----------------------------------------------------------------------------------

    fn add_session(&mut self, sample: &Sample) {
        if (self.session.as_ref()).is_some_and(|session| sample.game != session.game) {
            self.write_session(sample.at, true, true);
            self.session = None;
        }
        if sample.game.is_none() {
            return;
        }
        if self.session.is_none() {
            let settings = &self.settings;
            let session = Session::new(sample, &settings.fps_levels, settings.usual_levels);
            self.session = Some(session);
            self.write_session(sample.at, false, false);
        }
        let Some(session) = &mut self.session else {
            return;
        };
        session.add(sample);
        if sample.at - session.written >= SESSION_WRITE_S {
            self.write_session(sample.at, false, false);
        }
    }

    fn write_session(&mut self, at: f64, closed: bool, report: bool) {
        let Some(session) = &mut self.session else {
            return;
        };
        session.written = at;
        self.store.put(
            &insert("sessions", &session_columns()),
            session.row(at, closed),
        );
        let Some(on_report) = (self.on_report.clone()).filter(|_| closed && report) else {
            return;
        };
        if self.settings.report {
            // on the store's thread, after the row above: the report reads it from the file
            let (id, stamp) = (session.id, (self.stamp)());
            let (min_s, compare) = (self.settings.report_min_s, self.settings.report_compare);
            self.store.run(Box::new(move |con| {
                if let Some(found) = round_report::build(con, Some(id), min_s, compare)? {
                    on_report(found, stamp);
                }
                Ok(())
            }));
        }
    }
}

// -- the service -----------------------------------------------------------------------------------

struct State {
    reader: Reader,
    watch: fancontrol::Watch,
    recorder: Recorder,
    stopped: bool,
    /// the fingerprint of the FanControl state stored last
    mark: Option<String>,
    /// the names of the sensors, as stored last
    sensors: String,
    sensors_due: f64,
    /// the game now under way, for the status
    game: String,
    /// a report is on the panel
    shown: bool,
    /// the readings it published
    keys: HashSet<String>,
    /// the readings of the round under way (`session.*`)
    round: HashSet<String>,
    /// on the clock: when it goes
    until: f64,
    /// on the clock: when its readings go, after it has ended
    gone_at: f64,
    /// what went wrong with FanControl's file, as said last
    failure: Option<String>,
}

struct Shared {
    state: Mutex<State>,
    settings: Settings,
    host: ServiceHost,
    store: Arc<Store>,
    database: std::path::PathBuf,
    began: Instant,
    /// how many times a mode began: a report is stale when a game came since
    games: Arc<AtomicU64>,
}

fn wall() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64())
}

impl Shared {
    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    fn transition(&self) -> Option<Transition> {
        let name = &self.settings.report_transition;
        (!name.is_empty()).then(|| Transition::named(name))
    }

    fn want(&self, state: &State) {
        let mut keys: Vec<String> = WANTED.map(String::from).to_vec();
        keys.extend(self.settings.temperatures.iter().cloned());
        let mut sensors: Vec<&String> = state.reader.sources.values().collect();
        sensors.sort();
        sensors.dedup();
        keys.extend(sensors.iter().map(|sensor| format!("lhm:{sensor}")));
        if self.settings.music {
            // (not the title: nothing here reads it)
            keys.extend(["media.state", "media.playing"].map(String::from));
        }
        let keys: Vec<&str> = keys.iter().map(String::as_str).collect();
        self.host.want(&keys);
    }

    // -- every sample ----------------------------------------------------------------------------

    fn on_sample(&self, snapshot: &Snapshot) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        if state.stopped {
            return;
        }
        for note in self.store.notes() {
            self.host.note(&note);
        }
        let now = wall();
        self.expire(&mut state);
        self.tidy(&mut state);
        self.look_at_fancontrol(&mut state, now);
        if state.recorder.due(now) {
            let sample = state.reader.read(snapshot, now);
            state.game = sample.game.clone().unwrap_or_default();
            state.recorder.add(&sample);
            self.publish_round(&mut state, sample.at);
            self.note_sensors(&mut state, snapshot, now);
        }
    }

    /// The totals of the game being played as `session.*` readings, once a second; gone when
    /// the game is (their ranges may change with the options, so none is kept).
    fn publish_round(&self, state: &mut State, at: f64) {
        let game = state.recorder.running();
        let readings = game.map(|game| round_report::session_readings(&game.totals(at)));
        let readings = readings.unwrap_or_default();
        for key in state
            .round
            .iter()
            .filter(|key| !readings.contains_key(*key))
        {
            self.host.unpublish(key);
        }
        state.round = readings.keys().cloned().collect();
        for (key, (value, unit, label)) in readings {
            self.host.publish(&key, value, &unit, &label);
        }
    }

    /// Keep the names of the sensors, for the cooling analysis; written when they change.
    fn note_sensors(&self, state: &mut State, snapshot: &Snapshot, now: f64) {
        if !self.settings.cooling || now < state.sensors_due {
            return;
        }
        state.sensors_due = now + SENSORS_EVERY_S;
        let found = state.reader.sensors(&snapshot.readings);
        let none = |key: &str| !found.get(key).is_some_and(Value::truthy);
        if none("sensors") && none("cpu") && none("gpu") {
            return;
        }
        let text = fancontrol::state_text(&found);
        if text != state.sensors {
            state.sensors.clone_from(&text);
            let sql = "INSERT OR REPLACE INTO meta VALUES ('sensors', ?)";
            self.store.put(sql, vec![Value::Str(text)]);
        }
    }

    /// Note a changed FanControl configuration: the names of the fans and what each curve
    /// reads for the samples, and the state itself for the analysis, which looks at the data
    /// since the last change.
    fn look_at_fancontrol(&self, state: &mut State, now: f64) {
        let section = state.watch.poll(now);
        for note in std::mem::take(&mut state.watch.notes) {
            self.host.note(&note);
        }
        let section = match section {
            Ok(section) => section,
            Err(failure) => {
                if state.failure.as_ref() != Some(&failure) {
                    self.host.note(&format!("memory: FanControl: {failure}"));
                    state.failure = Some(failure);
                }
                None
            }
        };
        let Some(section) = section else {
            return;
        };
        let settled = fancontrol::state_of(&section);
        (state.reader.names, state.reader.hidden) = fancontrol::names_and_hidden(&section);
        let (curves, _fans) = fancontrol::curves_and_fans(&settled);
        state.reader.sources = (curves.values())
            .filter_map(|curve| Some((curve.name.clone(), curve.source.clone()?)))
            .collect();
        self.want(state);
        if !self.settings.cooling {
            return;
        }
        let mark = fancontrol::fingerprint(&settled);
        let before = (state.mark.take()).unwrap_or_else(|| last_fingerprint(&self.database));
        if mark != before {
            let text = fancontrol::state_text(&settled);
            self.store.put(
                "INSERT OR REPLACE INTO fancontrol VALUES (?, ?, ?)",
                vec![cut(now), Value::Str(mark.clone()), Value::Str(text)],
            );
        }
        state.mark = Some(mark);
    }

    // -- the report ------------------------------------------------------------------------------

    /// A round report has been built (on the store's thread): show it, unless a game has
    /// begun since, which the report would only be in the way of.
    fn report_ready(&self, report: Dict, games: u64) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        let game = self.host.mode().as_deref() == Some(GAME);
        if state.stopped || game || games != self.games.load(Ordering::Relaxed) {
            return;
        }
        self.show(&mut state, report);
    }

    /// Publish the report and put its theme on the panel for `report_seconds`. A second
    /// report while the first is on show only replaces its content.
    fn show(&self, state: &mut State, mut report: Dict) {
        let s = &self.settings;
        report.insert("seconds".into(), Value::Float(s.report_seconds));
        report.insert("until".into(), Value::Float(wall() + s.report_seconds));
        let readings = match round_report::readings(&report) {
            Ok(readings) => readings,
            Err(why) => {
                self.host.note(&format!("memory: the round report: {why}"));
                return;
            }
        };
        let again = std::mem::replace(&mut state.shown, true);
        state.until = self.clock() + s.report_seconds;
        let label = lp_i18n::t("Round report");
        let _ = (self.host).publish_state("report", &Value::Dict(report.clone()), &label);
        // (levels of another range)
        for key in state.keys.iter().filter(|key| !readings.contains_key(*key)) {
            self.host.unpublish(key);
        }
        state.keys = readings.keys().cloned().collect();
        for (key, (value, unit, label)) in readings {
            self.host.publish(&key, value, &unit, &label);
        }
        if !again && !s.report_theme.is_empty() {
            // in every mode but a game's (which ends it): ahead of the music's page too
            (self.host).show_theme(&s.report_theme, self.transition(), 10, Scope::Any);
        }
        let empty = Dict::default();
        let part = |key: &str| report.get(key).and_then(Value::as_dict).unwrap_or(&empty);
        let session = part("session");
        let number = |value: Option<&Value>| value.and_then(Value::to_f64);
        let minutes = (number(session.get("duration_s")).unwrap_or(0.0) / 60.0).floor();
        let fps = number(session.get("fps_mean")).filter(|fps| *fps != 0.0);
        let rounds = part("comparison").get("count").map(Value::str);
        self.host.note(&format!(
            "round report: {}, {minutes:.0} min, {} fps, against {} rounds",
            session.get("game").map(Value::str).unwrap_or_default(),
            fps.map_or("-".to_string(), |fps| format!("{fps:.0}")),
            rounds.unwrap_or_default(),
        ));
    }

    fn expire(&self, state: &mut State) {
        if state.shown && self.clock() >= state.until {
            self.end_report(state);
        }
    }

    /// The report is over: its state goes at once, its theme leaves, and the readings the
    /// theme shows stay a moment longer, for as long as it takes to leave.
    fn end_report(&self, state: &mut State) {
        if !std::mem::replace(&mut state.shown, false) {
            return;
        }
        let _ = self.host.publish_state("report", &Value::None, "");
        let themed = !self.settings.report_theme.is_empty();
        state.gone_at = self.clock() + if themed { REPORT_GRACE_S } else { 0.0 };
        if themed {
            self.host.restore_theme(self.transition());
        }
        self.tidy(state);
    }

    /// Take the readings of a report that is over back, once the grace is up.
    fn tidy(&self, state: &mut State) {
        if !state.keys.is_empty() && !state.shown && self.clock() >= state.gone_at {
            for key in state.keys.drain() {
                self.host.unpublish(&key);
            }
        }
    }

    /// A game began: the report goes at once, and none that was being built is shown. (The
    /// pages Libre Panel switches to itself, music and idle, are no game.)
    fn on_mode(&self, mode: Option<&str>) {
        if mode != Some(GAME) {
            return;
        }
        if let Ok(mut state) = self.state.lock() {
            self.games.fetch_add(1, Ordering::Relaxed);
            self.end_report(&mut state);
        }
    }
}

/// Keeps what happens over time and shows a report after a game.
pub struct Memory {
    shared: Arc<Shared>,
}

fn options() -> Schema {
    let levels = DEFAULT_LEVELS.map(|level| Value::int(level as i64));
    let text = |text: &str| Value::Str(text.into());
    vec![
        ("keep_days", "number", Value::Float(730.0)),
        ("cooling", "bool", Value::Bool(true)),
        ("cooling_days", "number", Value::Float(180.0)),
        ("music", "bool", Value::Bool(true)),
        ("temperatures", "list", Value::List(Vec::new())),
        ("fps_levels", "list", Value::List(levels.to_vec())),
        ("report", "bool", Value::Bool(true)),
        ("report_minutes", "number", Value::Float(3.0)),
        ("report_compare", "int", Value::int(10)),
        ("report_seconds", "number", Value::Float(30.0)),
        ("report_theme", "string", text("round-report")),
        ("report_transition", "string", text("tube")),
        ("fancontrol", "string", text("")),
        ("database", "string", text("")),
    ]
}

/// Where the database is: the file named in the options, else the service's own.
fn database_of(
    given: &str,
    host: &ServiceHost,
    places: &Places,
) -> Result<std::path::PathBuf, String> {
    if given.is_empty() {
        let folder = host
            .data_dir()
            .ok_or("memory: no folder to keep its data in")?;
        return Ok(folder.join(DATABASE));
    }
    let homes = paths::Homes {
        env: &*places.env,
        user: &paths::user_home,
    };
    let path = (Path::new(HERE, given).expanduser(&homes))
        .ok_or("RuntimeError: Could not determine home directory.")?;
    Ok(std::path::PathBuf::from(path.text()))
}

/// The memory as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let mut notes = Vec::new();
            let settings = Settings::from_options(&options, &mut |note| notes.push(note));
            for note in notes {
                host.note(&note);
            }
            let text = |key: &str| options.get(key).map(Value::str).unwrap_or_default();
            let database = database_of(&text("database"), &host, &Places::here())?;
            let store = Arc::new(Store::new(&database));
            let games = Arc::new(AtomicU64::new(0));
            let shared = Arc::new_cyclic(|shared: &std::sync::Weak<Shared>| {
                let shown = shared.clone();
                let on_report: OnReport = Arc::new(move |report, stamp| {
                    if let Some(shared) = shown.upgrade() {
                        shared.report_ready(report, stamp);
                    }
                });
                let counted = Arc::clone(&games);
                let stamp: Stamp = Arc::new(move || counted.load(Ordering::Relaxed));
                let recorder = Recorder::new(Arc::clone(&store), &settings, Some(on_report), stamp);
                Shared {
                    state: Mutex::new(State {
                        reader: Reader::new(&settings),
                        watch: fancontrol::Watch::new(&text("fancontrol"), Places::here()),
                        recorder,
                        stopped: false,
                        mark: None,
                        sensors: String::new(),
                        sensors_due: 0.0,
                        game: String::new(),
                        shown: false,
                        keys: HashSet::new(),
                        round: HashSet::new(),
                        until: 0.0,
                        gone_at: 0.0,
                        failure: None,
                    }),
                    settings: settings.clone(),
                    host,
                    store: Arc::clone(&store),
                    database,
                    began: Instant::now(),
                    games: Arc::clone(&games),
                }
            });
            Ok(Box::new(Memory { shared }))
        },
    }
}

impl Memory {
    /// show_last_report: build and show the report of the latest game that was long enough
    /// again (for a console page: "show the last round report"). False where nothing is
    /// recorded.
    pub fn show_last_report(&self) -> bool {
        let shared = Arc::clone(&self.shared);
        if shared.state.lock().map_or(true, |state| state.stopped) {
            return false;
        }
        let games = shared.games.load(Ordering::Relaxed);
        let (min_s, compare) = (shared.settings.report_min_s, shared.settings.report_compare);
        self.shared.store.run(Box::new(move |con| {
            if let Some(found) = round_report::build(con, None, min_s, compare)? {
                shared.report_ready(found, games);
            }
            Ok(())
        }));
        true
    }
}

impl Service for Memory {
    fn start(&mut self) -> Result<(), String> {
        let shared = &self.shared;
        shared.store.start();
        if let Ok(state) = shared.state.lock() {
            shared.want(&state);
        }
        let sampled = Arc::clone(shared);
        shared.host.on(
            "sample",
            Box::new(move |event| {
                if let Event::Sample { snapshot } = event {
                    sampled.on_sample(snapshot);
                }
            }),
        )?;
        let changed = Arc::clone(shared);
        shared.host.on(
            "mode-changed",
            Box::new(move |event| {
                if let Event::ModeChanged { mode } = event {
                    changed.on_mode(mode.as_deref());
                }
            }),
        )?;
        let database = shared.database.display();
        shared.host.note(&format!("memory: {database}"));
        Ok(())
    }

    fn stop(&mut self) {
        if let Ok(mut state) = self.shared.state.lock() {
            // (a sample under way, or a report being built, then does nothing)
            state.stopped = true;
            state.recorder.close(wall());
        }
        self.shared.store.stop(3.0);
        for note in self.shared.store.notes() {
            self.shared.host.note(&note);
        }
    }

    /// What the service is doing, for a console page.
    fn summary(&self) -> Option<Value> {
        let state = self.shared.state.lock().ok()?;
        let store = &self.shared.store;
        let mut said = Dict::default();
        said.insert("recording".into(), Value::Bool(!state.stopped));
        let database = self.shared.database.display().to_string();
        said.insert("database".into(), Value::Str(database));
        let written = i64::try_from(store.written()).unwrap_or(i64::MAX);
        said.insert("written".into(), Value::int(written));
        said.insert(
            "error".into(),
            store.error().map_or(Value::None, Value::Str),
        );
        let game = Some(&state.game).filter(|game| !game.is_empty());
        said.insert("game".into(), text(game));
        said.insert("report".into(), Value::Bool(state.shown));
        Some(Value::Dict(said))
    }
}

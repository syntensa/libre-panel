//! The readings a frame is drawn of: the sources that are read (sensors/base.py's SensorHub
//! and its history) and the computer's own, under the names sensors/psutil_provider.py gives
//! them: the processor's load, clock and threads, the memory, the system's disk, the
//! network's and the disks' rates, how long the computer runs and the processor's
//! temperature (system.rs reads them from the system).

use std::collections::{BTreeSet, VecDeque};
use std::time::Instant;

use indexmap::IndexMap;
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
pub trait Source: Send {
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
    /// Keys this source gives even when a source earlier in the list has them too: for what
    /// the user said outright, or what is surely right.
    fn overrides(&self) -> &'static [&'static str] {
        &[]
    }
    /// status: what the source says about itself (how it reads, what keeps it from reading), as
    /// the app's Control page lists it; None for a source that says nothing.
    fn status(&self) -> Option<lp_py::Dict> {
        None
    }
    /// The pictures the source holds now, by their keys (an album's cover).
    fn images(&self) -> IndexMap<String, lp_pixels::Image> {
        IndexMap::new()
    }
    /// Which pictures `images` gives: 0 for none, else a number that changes with them.
    fn images_state(&self) -> u64 {
        0
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

/// A dict of `(key, value)` in order.
fn told(pairs: Vec<(&str, Value)>) -> lp_py::Dict {
    pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

fn text_or_none(text: Option<String>) -> Value {
    text.map_or(Value::None, Value::Str)
}

fn bool_or_none(flag: Option<bool>) -> Value {
    flag.map_or(Value::None, Value::Bool)
}

/// LhmStatus as dataclasses.asdict gives it, without what the pages do not show (its rounds'
/// timing and the service's memory).
pub fn lhm_status_dict(status: &lp_sensors::service::LhmStatus) -> lp_py::Dict {
    told(vec![
        ("mode", Value::Str(status.mode.clone())),
        ("elevated", bool_or_none(status.elevated)),
        ("ring0", bool_or_none(status.ring0)),
        ("pawnio", text_or_none(status.pawnio.clone())),
        ("error", Value::Str(status.error.clone())),
        ("service", Value::Str(status.service.clone())),
        ("version", Value::Str(status.version.clone())),
    ])
}

impl Source for Lhm {
    fn name(&self) -> &'static str {
        "librehardwaremonitor"
    }

    fn status(&self) -> Option<lp_py::Dict> {
        Some(lhm_status_dict(&self.0.status()))
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

/// Frames per second of the game, through PresentMon (sensors/presentmon.py).
struct PresentMon(lp_sensors::presentmon::PresentMonSource);

impl Source for PresentMon {
    fn name(&self) -> &'static str {
        "presentmon"
    }

    fn read(&mut self, out: &mut Readings) {
        // (the game the `game` service found: its frames are the ones that count)
        self.0
            .set_target(lp_sensors::presentmon::target().as_deref());
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }
}

/// Frames per second of the game from MangoHud's log (sensors/mangohud.py).
struct MangoHud(lp_sensors::mangohud::MangoHudSource);

impl MangoHud {
    fn of(sensors: &lp_config::SensorsConfig, wanted: &Wanted) -> MangoHud {
        let folder = text(sensors.options.get("mangohud"), "folder");
        MangoHud(lp_sensors::mangohud::MangoHudSource::new(
            folder,
            wanted.clone(),
        ))
    }
}

impl Source for MangoHud {
    fn name(&self) -> &'static str {
        "mangohud"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }
}

/// Frames per second of the game from RivaTuner's shared memory (sensors/rtss.py).
struct Rtss(lp_sensors::rtss::RtssSource);

impl Source for Rtss {
    fn name(&self) -> &'static str {
        "rtss"
    }

    fn status(&self) -> Option<lp_py::Dict> {
        let runs = self.0.status().map(|runs| match runs {
            true => "runs".to_string(),
            false => "not running".to_string(),
        });
        Some(told(vec![("rivatuner", text_or_none(runs))]))
    }

    fn read(&mut self, out: &mut Readings) {
        self.0
            .set_target(lp_sensors::presentmon::target().as_deref());
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }
}

/// What HWiNFO shares of its sensors (sensors/hwinfo.py).
struct Hwinfo(lp_sensors::hwinfo::HwinfoSource);

impl Source for Hwinfo {
    fn name(&self) -> &'static str {
        "hwinfo"
    }

    fn status(&self) -> Option<lp_py::Dict> {
        Some(told(vec![
            ("state", Value::Str(self.0.state.into())),
            ("readings", Value::int(self.0.count() as i64)),
        ]))
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }
}

/// What is playing, and its cover (sensors/media.py).
struct Media(lp_sensors::media::MediaSource);

impl Source for Media {
    fn name(&self) -> &'static str {
        "media"
    }

    fn status(&self) -> Option<lp_py::Dict> {
        let status = self.0.status();
        Some(told(vec![
            ("read with", text_or_none(status.read_with)),
            ("now", text_or_none(status.now)),
            ("error", text_or_none(status.error)),
        ]))
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }

    fn notes(&mut self) -> Vec<String> {
        self.0.notes()
    }

    // News shows in the next frame (a new song, play or pause), and the position moves on
    // at each frame's time: reading is a copy of the track the thread keeps.
    fn every_frame(&self) -> Every {
        Every::Frame
    }

    fn images(&self) -> IndexMap<String, lp_pixels::Image> {
        self.0.images()
    }

    fn images_state(&self) -> u64 {
        self.0.images_state()
    }
}

/// The weather of the user's place, from Open-Meteo (weather/open_meteo.py).
struct Weather(lp_sensors::weather::WeatherSource);

impl Source for Weather {
    fn name(&self) -> &'static str {
        "open-meteo"
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
}

/// What the program names itself to the servers it asks.
static AGENT: std::sync::LazyLock<String> =
    std::sync::LazyLock::new(|| format!("libre-panel/{}", lp_py::VERSION));

/// A value of config.toml as Python has it (dates and times as their texts).
pub fn value_of(toml: &Toml) -> lp_py::Value {
    use lp_py::Value;
    let clock = |time: &lp_py::toml::Time| {
        let mut text = format!("{:02}:{:02}:{:02}", time.hour, time.minute, time.second);
        if time.microsecond != 0 {
            text.push_str(&format!(".{:06}", time.microsecond));
        }
        text
    };
    let day =
        |date: &lp_py::toml::Date| format!("{:04}-{:02}-{:02}", date.year, date.month, date.day);
    match toml {
        Toml::Bool(on) => Value::Bool(*on),
        Toml::Int(n) => Value::Int(n.clone()),
        Toml::Float(f) => Value::Float(*f),
        Toml::Str(text) => Value::Str(text.clone()),
        Toml::Array(list) => Value::List(list.iter().map(value_of).collect()),
        Toml::Table(table) => Value::Dict(
            table
                .iter()
                .map(|(key, value)| (key.clone(), value_of(value)))
                .collect(),
        ),
        Toml::Date(date) => Value::Str(day(date)),
        Toml::Time(time) => Value::Str(clock(time)),
        Toml::DateTime(date, time, offset) => {
            let mut text = format!("{} {}", day(date), clock(time));
            if let Some(minutes) = offset {
                let sign = if *minutes < 0 { '-' } else { '+' };
                let minutes = minutes.abs();
                text.push_str(&format!("{sign}{:02}:{:02}", minutes / 60, minutes % 60));
            }
            Value::Str(text)
        }
    }
}

/// `float(table.get(key, fallback))`; why it is no number.
fn seconds_of(table: Option<&Table>, key: &str, fallback: f64) -> Result<f64, String> {
    let number = match table.and_then(|table| table.get(key)) {
        None => Some(fallback),
        Some(Toml::Float(f)) => Some(*f),
        Some(Toml::Int(n)) => lp_py::num::int_to_float(n).ok(),
        Some(Toml::Bool(on)) => Some(f64::from(u8::from(*on))),
        Some(Toml::Str(text)) => lp_py::float_of_str(text),
        Some(_) => None,
    };
    number.ok_or_else(|| format!("{key} is no number"))
}

/// Home Assistant's entities (sensors/homeassistant.py).
struct HomeAssistant(lp_sensors::homeassistant::HomeAssistantSource);

impl Source for HomeAssistant {
    fn name(&self) -> &'static str {
        "homeassistant"
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
}

/// Values from addresses and files that hold JSON (sensors/json_source.py).
struct Json(lp_sensors::jsonsource::JsonSource);

impl Source for Json {
    fn name(&self) -> &'static str {
        "json"
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
}

/// The UPS, through NUT's upsd (sensors/nut.py).
struct Nut(lp_sensors::nut::NutSource);

impl Source for Nut {
    fn name(&self) -> &'static str {
        "nut"
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
}

/// What devices publish to an MQTT broker (sensors/mqtt.py).
struct Mqtt(lp_sensors::mqtt::MqttSource);

impl Source for Mqtt {
    fn name(&self) -> &'static str {
        "mqtt"
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
}

/// The next events of iCalendar addresses and files (sensors/calendar.py).
struct Calendar(lp_sensors::calendar::CalendarSource);

impl Source for Calendar {
    fn name(&self) -> &'static str {
        "calendar"
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
}

/// `int(table.get(key) or fallback)` as a port; none where it is none.
fn port_of(value: Option<&Toml>, fallback: u16) -> Option<u16> {
    let port = match value {
        Some(Toml::Int(n)) => u16::try_from(n).ok()?,
        Some(Toml::Float(f)) if f.is_finite() => u16::try_from(f.trunc() as i64).ok()?,
        Some(Toml::Str(text)) if !text.is_empty() => {
            lp_py::int_of_str(text).and_then(|n| u16::try_from(n).ok())?
        }
        Some(Toml::Bool(_) | Toml::Str(_)) | None => 0,
        Some(_) => return None,
    };
    Some(if port == 0 { fallback } else { port })
}

/// The source `name` (homeassistant, json, nut, mqtt) as its table of config.toml sets it
/// up; why it could not be.
fn web_source(name: &str, table: Option<&Table>) -> Result<Box<dyn Source>, String> {
    let get = |key: &str| table.and_then(|table| table.get(key));
    if name == "calendar" {
        let sources = match get("sources") {
            None => Vec::new(),
            Some(Toml::Str(one)) => vec![one.clone()],
            Some(Toml::Array(list)) => list.iter().map(|one| value_of(one).str()).collect(),
            Some(Toml::Table(keys)) => keys.keys().cloned().collect(),
            Some(_) => return Err("sources is no list".into()),
        };
        // `int(options.get("days", 14))`
        let days = match get("days") {
            None => 14,
            Some(Toml::Int(n)) => i64::try_from(n).unwrap_or(i64::MAX),
            Some(Toml::Float(f)) if f.is_finite() => f.trunc() as i64,
            Some(Toml::Bool(on)) => i64::from(*on),
            Some(Toml::Str(text)) => lp_py::int_of_str(text)
                .map(|n| i64::try_from(n).unwrap_or(i64::MAX))
                .ok_or("days is no number")?,
            Some(_) => return Err("days is no number".into()),
        };
        let minutes = seconds_of(table, "update_minutes", 15.0)?;
        let source = lp_sensors::calendar::CalendarSource::new(sources, days, minutes, &AGENT);
        return Ok(Box::new(Calendar(source)));
    }
    if name == "mqtt" {
        let truthy = |key: &str| get(key).map(value_of).filter(lp_py::Value::truthy);
        let mut options = lp_sensors::mqtt::Options {
            username: truthy("username")
                .map(|name| name.str())
                .unwrap_or_default(),
            password: get("password")
                .map(|word| value_of(word).str())
                .unwrap_or_default(),
            tls: truthy("tls").is_some(),
            port: port_of(get("port"), 1883).ok_or("port is no port")?,
            ..Default::default()
        };
        if let Some(host) = get("host") {
            options.host = value_of(host).str();
        }
        options.topics = match get("topics") {
            None => Vec::new(),
            Some(Toml::Array(list)) => list.iter().map(|topic| value_of(topic).str()).collect(),
            Some(Toml::Str(text)) => text.chars().map(String::from).collect(),
            Some(Toml::Table(keys)) => keys.keys().cloned().collect(),
            Some(_) => return Err("topics is no list".into()),
        };
        match get("units") {
            Some(Toml::Table(units)) => {
                for (pattern, unit) in units {
                    options.units.insert(pattern.clone(), value_of(unit).str());
                }
            }
            Some(other) if value_of(other).truthy() => return Err("units is no table".into()),
            _ => {}
        }
        return Ok(Box::new(Mqtt(lp_sensors::mqtt::MqttSource::new(options))));
    }
    if name == "nut" {
        // `str(options.get(key) or "")`
        let text = |key: &str| match get(key).map(value_of) {
            Some(value) if value.truthy() => value.str(),
            _ => String::new(),
        };
        let mut options = lp_sensors::nut::Options {
            ups: text("ups"),
            username: text("username"),
            password: text("password"),
            update_s: seconds_of(table, "update_s", 5.0)?,
            ..Default::default()
        };
        if !text("host").is_empty() {
            options.host = text("host");
        }
        options.port = port_of(get("port"), lp_sensors::nut::PORT).ok_or("port is no port")?;
        return Ok(Box::new(Nut(lp_sensors::nut::NutSource::new(options))));
    }
    if name == "json" {
        let every = seconds_of(table, "update_s", 30.0)?;
        let entries = match get("sources") {
            Some(Toml::Array(list)) => list.iter().map(value_of).collect(),
            Some(Toml::Table(keys)) => keys.keys().cloned().map(lp_py::Value::Str).collect(),
            _ => Vec::new(),
        };
        let source = lp_sensors::jsonsource::JsonSource::new(&entries, every, &AGENT);
        return Ok(Box::new(Json(source)));
    }
    let mut options = lp_sensors::homeassistant::Options::default();
    if let Some(url) = get("url") {
        options.url = value_of(url).str();
    }
    if let Some(token) = get("token") {
        options.token = value_of(token).str();
    }
    match get("entities") {
        None => {}
        Some(Toml::Str(one)) => options.entities = vec![one.clone()],
        Some(Toml::Array(list)) => {
            options.entities = list.iter().map(|entity| value_of(entity).str()).collect();
        }
        Some(Toml::Table(keys)) => options.entities = keys.keys().cloned().collect(),
        Some(_) => return Err("entities is no list".into()),
    }
    options.poll_seconds = seconds_of(table, "poll_seconds", 10.0)?;
    let source = lp_sensors::homeassistant::HomeAssistantSource::new(options, &AGENT);
    Ok(Box::new(HomeAssistant(source)))
}

/// What the services publish (plugins/host.py's _HostProvider): cheap, so a published
/// reading reaches the next frame.
pub struct Services(pub std::sync::Arc<lp_services::host::PluginHost>);

impl Source for Services {
    fn name(&self) -> &'static str {
        "services"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.published());
    }

    fn every_frame(&self) -> Every {
        Every::Frame
    }

    fn images(&self) -> IndexMap<String, lp_pixels::Image> {
        self.0.images()
    }

    fn images_state(&self) -> u64 {
        self.0.images_state()
    }
}

/// The sun and the moon (sensors/sky.py).
struct Sky(lp_sensors::sky::SkySource);

impl Source for Sky {
    fn name(&self) -> &'static str {
        "sky"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }
}

/// Temperatures, fans, power and the graphics card of a Linux PC (sensors/linux.py).
struct Linux(lp_sensors::linux::LinuxSource);

impl Source for Linux {
    fn name(&self) -> &'static str {
        "linux"
    }

    fn status(&self) -> Option<lp_py::Dict> {
        let status = self.0.status();
        let nvml = status.nvml.then(|| "NVML".to_string());
        Some(told(vec![
            ("chips", Value::int(status.chips as i64)),
            ("temperatures", Value::int(status.temperatures as i64)),
            ("fans", Value::int(status.fans as i64)),
            ("graphics", text_or_none(status.graphics)),
            ("nvidia driver", text_or_none(nvml)),
            (
                "cpu power",
                text_or_none(status.cpu_power.map(str::to_string)),
            ),
        ]))
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }
}

/// The names of the processor, the graphics card and the memory, and the memory's speed
/// (sensors/hardware.py): always on, and what the user named under `[hardware]` instead.
struct Hardware(lp_sensors::hardware::HardwareSource);

impl Hardware {
    fn of(config: &lp_config::Config) -> Hardware {
        let card = match config.sensors.options.get("librehardwaremonitor") {
            Some(table) => match table.get("gpu") {
                Some(Toml::Str(card)) => card.as_str(),
                _ => "",
            },
            None => "",
        };
        let hardware = &config.hardware;
        let custom = [
            hardware.cpu_name.as_str(),
            hardware.gpu_name.as_str(),
            hardware.memory_name.as_str(),
        ];
        Hardware(lp_sensors::hardware::HardwareSource::new(custom, card))
    }
}

impl Source for Hardware {
    fn name(&self) -> &'static str {
        "hardware"
    }

    fn read(&mut self, out: &mut Readings) {
        out.extend(self.0.read());
    }

    fn overrides(&self) -> &'static [&'static str] {
        &lp_sensors::hardware::NAME_KEYS
    }
}

/// A text of a source's table in config.toml, stripped; "" where there is none.
fn text<'a>(table: Option<&'a Table>, key: &str) -> &'a str {
    match table.and_then(|table| table.get(key)) {
        Some(Toml::Str(text)) => lp_py::strip(text),
        _ => "",
    }
}

impl std::fmt::Debug for Hub {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Hub")
            .field("sources", &self.names())
            .finish_non_exhaustive()
    }
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
    /// each source's pictures as they were last taken: their state and their keys
    pictures: Vec<(u64, Vec<String>)>,
    /// the fans by the names they go by
    names: crate::fan_names::FanNames,
    /// the keys each panel's loop reads (0: the first panel's, n + 1: `[[panels]]` n's)
    wants: IndexMap<usize, BTreeSet<String>>,
    /// when the readings were taken last, and they and their pictures, for the panels beside
    /// the first (`shared`)
    taken: Option<(Instant, Readings, Images)>,
}

pub type Images = IndexMap<String, lp_pixels::Image>;

/// The readings with their numbers added to `history`, as a snapshot's histories (remember).
fn remember(history: &mut IndexMap<String, VecDeque<f64>>, readings: &Readings) -> Histories {
    for (key, reading) in readings {
        let number = match &reading.value {
            Value::Float(f) => Some(*f),
            Value::Int(n) => lp_py::num::int_to_float(n).ok(),
            _ => None,
        };
        if let Some(number) = number {
            let series = history.entry(key.clone()).or_default();
            if series.len() == HISTORY_LENGTH {
                series.pop_front();
            }
            series.push_back(number);
        }
    }
    history
        .iter()
        .map(|(key, series)| (key.clone(), series.iter().copied().collect()))
        .collect()
}

type Histories = IndexMap<String, Vec<f64>>;

impl Hub {
    pub fn new(sources: Vec<Box<dyn Source>>) -> Hub {
        Hub {
            sources,
            history: IndexMap::new(),
            wanted: Wanted::default(),
            notes: Vec::new(),
            owners: IndexMap::new(),
            pictures: Vec::new(),
            names: crate::fan_names::FanNames::default(),
            wants: IndexMap::new(),
            taken: None,
        }
    }

    /// config.toml was loaded: `[hardware]`, which no source's own table holds (the names of
    /// the processor, card and memory, and of the fans).
    pub fn follow_config(&mut self, config: &lp_config::Config) {
        self.names.follow_config(config);
        for source in &mut self.sources {
            if source.name() == "hardware" {
                *source = Box::new(Hardware::of(config));
            }
        }
    }

    /// The pictures of the sources laid into `images` where they changed since they were last
    /// taken (a new cover with its new title, not a second later), and taken out where a
    /// source has one no more. With `all` every source's, else those of the sources that
    /// follow on every frame (without `video` not those that do so only in video mode).
    pub fn refresh_images(
        &mut self,
        images: &mut IndexMap<String, lp_pixels::Image>,
        all: bool,
        video: bool,
    ) {
        refresh(&self.sources, &mut self.pictures, images, all, video);
    }

    /// The hub made anew (config.toml's sources changed): what the loops read is kept.
    pub fn replace(&mut self, mut fresh: Hub) {
        fresh.wants = std::mem::take(&mut self.wants);
        fresh.wanted.set(fresh.wants.values().flatten().cloned());
        *self = fresh;
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
        self.names.apply(&mut readings);
        readings
    }

    /// One more source, asked after those there are.
    pub fn add(&mut self, source: Box<dyn Source>) {
        self.sources.push(source);
    }

    /// Whether any source wants to be read on every frame.
    pub fn every_frame(&self) -> bool {
        self.sources
            .iter()
            .any(|source| source.every_frame() != Every::No)
    }

    /// The keys (and patterns) the themes on show read: `owner`'s (0: the first panel's loop,
    /// n + 1: that of `[[panels]]` n) and the others' (want: each loop's, forgotten with it).
    pub fn want(&mut self, owner: usize, keys: impl IntoIterator<Item = String>) {
        self.wants.insert(owner, keys.into_iter().collect());
        self.wanted.set(self.wants.values().flatten().cloned());
    }

    /// What a loop that ended read: not wanted any more.
    pub fn forget(&mut self, owner: usize) {
        if self.wants.shift_remove(&owner).is_some() {
            self.wanted.set(self.wants.values().flatten().cloned());
        }
    }

    /// age: how long ago the readings were last taken, in seconds (None: not yet).
    pub fn age(&self) -> Option<f64> {
        self.taken
            .as_ref()
            .map(|(at, _, _)| at.elapsed().as_secs_f64())
    }

    /// shared: for another panel (`View`), the readings and pictures taken last where they
    /// are younger than `max_age` seconds, else new ones: the sources are not read twice for
    /// two panels that draw at the same pace.
    pub fn shared(&mut self, max_age: f64) -> (Readings, Images) {
        if let Some((at, readings, images)) = &self.taken {
            if at.elapsed().as_secs_f64() < max_age {
                return (readings.clone(), images.clone());
            }
        }
        self.take()
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

    /// sources: each source the panel reads, the first that has a reading winning: its name,
    /// how many readings of the last snapshot came from it and what it says of itself.
    pub fn sources(&self) -> Vec<(&'static str, usize, Option<lp_py::Dict>)> {
        (self.sources.iter().enumerate())
            .map(|(n, source)| {
                let count = self.owners.values().filter(|owner| **owner == n).count();
                (source.name(), count, source.status())
            })
            .collect()
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
    pub fn of(config: &lp_config::Config, demo: bool) -> (Hub, Vec<String>) {
        let sensors = &config.sensors;
        let mut sources: Vec<Box<dyn Source>> = Vec::new();
        let mut missing = Vec::new();
        let mut notes = Vec::new();
        let wanted = Wanted::default();
        // `gpu` under `[sensors.linux]` (or, as everywhere else, under
        // `[sensors.librehardwaremonitor]`) picks the card by a part of its name
        let card = match text(sensors.options.get("linux"), "gpu") {
            "" => text(sensors.options.get("librehardwaremonitor"), "gpu"),
            card => card,
        }
        .to_string();
        if demo {
            sources.push(Box::new(Demo::new()));
        } else {
            for name in &sensors.providers {
                match name.as_str() {
                    "psutil" => {
                        let mut source =
                            crate::psutil::System::new(sensors.options.get(name), wanted.clone());
                        // the Linux source reads the kernel's chips: psutil leaves them
                        source.hwmon = !sensors.providers.iter().any(|name| name == "linux");
                        sources.push(Box::new(source));
                    }
                    "load" => sources.push(Box::new(Load(lp_sensors::load::LoadSource::of_card(
                        wanted.clone(),
                        &card,
                    )))),
                    "linux" => {
                        let nvidia = match text(sensors.options.get("linux"), "nvidia") {
                            "" => "auto",
                            nvidia => nvidia,
                        };
                        // (the tests' own: a machine laid out in a folder)
                        let source = match std::env::var_os("LP_LINUX_ROOT") {
                            None => {
                                lp_sensors::linux::LinuxSource::new(&card, nvidia, wanted.clone())
                            }
                            Some(root) => lp_sensors::linux::LinuxSource::at(
                                std::path::Path::new(&root),
                                &card,
                                nvidia,
                                wanted.clone(),
                                Box::new(|| None),
                                Box::new(Vec::new),
                            ),
                        };
                        sources.push(Box::new(Linux(source)));
                    }
                    "audio" => sources.push(Box::new(Audio(lp_sensors::audio::AudioSource::new(
                        wanted.clone(),
                    )))),
                    "presentmon" => {
                        let path = match sensors.options.get(name).and_then(|t| t.get("path")) {
                            Some(Toml::Str(path)) => path.clone(),
                            _ => String::new(),
                        };
                        sources.push(Box::new(PresentMon(
                            lp_sensors::presentmon::PresentMonSource::new(&path, wanted.clone()),
                        )));
                    }
                    "librehardwaremonitor" => {
                        let (source, said) = Lhm::new(sensors.options.get(name), &wanted);
                        sources.push(Box::new(source));
                        notes.extend(said);
                    }
                    "hardware" => sources.push(Box::new(Hardware::of(config))),
                    "homeassistant" | "json" | "nut" | "mqtt" | "calendar" => {
                        let table = sensors.options.get(name);
                        match web_source(name, table) {
                            Ok(source) => sources.push(source),
                            // (a misconfigured source must not stop the panel)
                            Err(why) => {
                                notes.push(format!("sensor source {name:?} not started: {why}"));
                            }
                        }
                    }
                    "mangohud" => sources.push(Box::new(MangoHud::of(sensors, &wanted))),
                    "rtss" => {
                        let mapping = text(sensors.options.get("rtss"), "mapping");
                        sources.push(Box::new(Rtss(lp_sensors::rtss::RtssSource::new(
                            mapping,
                            wanted.clone(),
                        ))));
                    }
                    "hwinfo" => {
                        sources.push(Box::new(Hwinfo(lp_sensors::hwinfo::HwinfoSource::new())));
                    }
                    "media" => sources.push(Box::new(Media(lp_sensors::media::MediaSource::new(
                        wanted.clone(),
                    )))),
                    "sky" => {
                        let table = sensors.options.get(name);
                        let number = |key: &str| match table.and_then(|t| t.get(key)) {
                            Some(Toml::Float(f)) => Some(*f),
                            Some(Toml::Int(n)) => lp_py::num::int_to_float(n).ok(),
                            _ => None,
                        };
                        let place = number("latitude").zip(number("longitude"));
                        sources.push(Box::new(Sky(lp_sensors::sky::SkySource::new(place))));
                    }
                    other => missing.push(other.to_string()),
                }
            }
            if let (true, Some(latitude), Some(longitude)) = (
                config.weather.enabled,
                config.weather.latitude,
                config.weather.longitude,
            ) {
                // (the tests' own: a forecast served by the test)
                let base = std::env::var("LP_WEATHER_URL")
                    .unwrap_or_else(|_| lp_sensors::weather::FORECAST_URL.to_string());
                sources.push(Box::new(Weather(lp_sensors::weather::WeatherSource::new(
                    &base,
                    latitude,
                    longitude,
                    config.weather.units == "imperial",
                    u64::try_from(&config.weather.update_minutes).unwrap_or(5),
                    &AGENT,
                ))));
            }
            if !sensors.providers.iter().any(|name| name == "sky") {
                // the sun for the weather's place (if set), the moon everywhere
                let place = config.weather.latitude.zip(config.weather.longitude);
                sources.push(Box::new(Sky(lp_sensors::sky::SkySource::new(place))));
            }
            if !sensors.providers.iter().any(|name| name == "media") {
                // idle until a theme shows what is playing
                sources.push(Box::new(Media(lp_sensors::media::MediaSource::new(
                    wanted.clone(),
                ))));
            }
            if cfg!(target_os = "linux") && !sensors.providers.iter().any(|n| n == "mangohud") {
                // a game's frame rate from MangoHud's log, read only while a theme shows it
                sources.push(Box::new(MangoHud::of(sensors, &wanted)));
            }
            if !sensors.providers.iter().any(|name| name == "hardware") {
                sources.push(Box::new(Hardware::of(config))); // the names of this PC
            }
        }
        let mut hub = Hub {
            wanted,
            notes,
            ..Hub::new(sources)
        };
        hub.names.follow_config(config);
        (hub, missing)
    }

    /// The sources that are read, by their names.
    pub fn names(&self) -> Vec<&'static str> {
        self.sources.iter().map(|source| source.name()).collect()
    }

    /// snapshot: every source read once, the numbers among the readings added to their
    /// histories, at the moment `now`.
    pub fn snapshot(&mut self, now: Moment, fold: bool) -> Snapshot {
        let (readings, images) = self.take();
        Snapshot {
            history: remember(&mut self.history, &readings),
            readings,
            now,
            fold,
            images,
        }
    }

    /// Every source read once: the readings and the pictures, kept for `shared`.
    fn take(&mut self) -> (Readings, Images) {
        let mut readings = Readings::new();
        let mut owners = IndexMap::new();
        for (n, source) in self.sources.iter_mut().enumerate() {
            let mut found = Readings::new();
            source.read(&mut found);
            let overrides = source.overrides();
            for (key, reading) in found {
                if !readings.contains_key(&key) || overrides.contains(&key.as_str()) {
                    owners.insert(key.clone(), n);
                    readings.insert(key, reading);
                }
            }
        }
        self.owners = owners;
        self.names.apply(&mut readings);
        // (every picture anew: the snapshot begins without any)
        let mut images = IndexMap::new();
        self.pictures.clear();
        self.refresh_images(&mut images, true, true);
        self.taken = Some((Instant::now(), readings.clone(), images.clone()));
        (readings, images)
    }
}

/// Hub::refresh_images on `pictures`, what was last taken of each source's (a view keeps its
/// own).
fn refresh(
    sources: &[Box<dyn Source>],
    pictures: &mut Vec<(u64, Vec<String>)>,
    images: &mut Images,
    all: bool,
    video: bool,
) {
    pictures.resize(sources.len(), (0, Vec::new()));
    for (n, source) in sources.iter().enumerate() {
        let follows = match source.every_frame() {
            Every::No => false,
            Every::Video => video,
            Every::Frame => true,
        };
        let state = source.images_state();
        if !(all || follows) || state == pictures[n].0 {
            continue;
        }
        for key in std::mem::take(&mut pictures[n].1) {
            images.shift_remove(&key);
        }
        let found = if state == 0 {
            IndexMap::new()
        } else {
            source.images()
        };
        pictures[n] = (state, found.keys().cloned().collect());
        for (key, image) in found {
            images.entry(key).or_insert(image);
        }
    }
}

/// HubView: a panel beside the first's way to the readings: the first panel's hub reads the
/// sources, and this panel takes what it read last where that is new enough (`SHARE_S`),
/// else has them read anew. Its history is its own, at the pace its theme takes readings.
#[derive(Default)]
pub struct View {
    history: IndexMap<String, VecDeque<f64>>,
    pictures: Vec<(u64, Vec<String>)>,
}

impl View {
    /// readings this old are new enough: a panel drawing at the same pace reads none
    pub const SHARE_S: f64 = 1.0;

    pub fn snapshot(&mut self, hub: &mut Hub, now: Moment, fold: bool) -> Snapshot {
        let (mut readings, images) = hub.shared(Self::SHARE_S);
        // what follows on every frame (the services' readings: an alarm) as it is now, not
        // as it was when the shared ones were taken
        readings.extend(hub.fresh(true));
        Snapshot {
            history: remember(&mut self.history, &readings),
            readings,
            now,
            fold,
            images,
        }
    }

    /// Hub::refresh_images, of what this panel took last.
    pub fn refresh_images(&mut self, hub: &Hub, images: &mut Images, video: bool) {
        refresh(&hub.sources, &mut self.pictures, images, false, video);
    }
}

/// Readings made up of the time alone, for a look at a theme without a computer's own
/// (`--demo`): the demo's (sensors/demo.py's DemoProvider, lp-services' demo).
pub struct Demo;

impl Demo {
    pub fn new() -> Demo {
        Demo
    }
}

impl Source for Demo {
    fn name(&self) -> &'static str {
        "demo"
    }

    fn read(&mut self, out: &mut Readings) {
        let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
        let t = since.map_or(0.0, |since| since.as_secs_f64());
        let utc = lp_py::zones::Zone::utc();
        let zone = lp_py::zones::local().unwrap_or(&utc);
        out.extend(lp_services::demo::read(t, zone));
    }
}

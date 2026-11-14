//! Waveshare's USB monitors (experimental): the display draws the theme on its own card, and
//! this service sends it Libre Panel's readings for it, once a second; with the brightness
//! and the screen turned upside down where they are set (services/waveshare.py). Nothing is
//! written on the card (lp-devices' waveshare has no command that would).
//!
//! `[services.waveshare]`:
//!
//! ```toml
//! port = ""          # "": the display is looked for; else its port ("COM5", "/dev/ttyACM0")
//! brightness = 0     # 1-100 sets it; 0 leaves it as it is
//! flip = ""          # "": as it is; "normal" or "upside-down"
//! units = false      # the values with their units, as Waveshare's program sends them
//! every_s = 1.0
//! theme = ""         # a theme on the card to show ("/data/theme/480x480/Space Suit.Theme")
//! rotate = []        # themes on the card shown in turn, at random,
//! rotate_min = 0     # each for so many minutes (0: no turns)
//! ```
//!
//! The display's themes name their values with the keys of Waveshare's own program, in
//! English (`Cpu_Temp`), and some with Chinese ones (`Cpu温度`): both are sent. Waveshare's
//! program writes its values with their units (`75%`, `24RPM`, `31℃`) and the weekday short
//! (`Mon`); here the numbers go alone unless `units` is on, since themes often show the units
//! themselves. Waveshare's program and this service cannot both have the display: the one
//! that opens its port first keeps it.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_devices::base::{leave_place, take_place, DeviceError};
use lp_devices::waveshare::{self, Display, Told};
use lp_py::datetime::DateTime;
use lp_py::{Dict, Value};
use lp_render::snapshot::Snapshot;

use crate::host::ServiceHost;
use crate::manager::{Part, Schema, Service};

/// how long a search that found nothing waits before the next
pub const RETRY_S: f64 = 5.0;
/// and one whose port did not answer as the display does (it is asked seldom)
pub const QUIET_S: f64 = 60.0;

fn number(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(on) => Some(f64::from(u8::from(*on))),
        Value::Int(_) | Value::Float(_) => value.to_f64(),
        _ => None,
    }
}

/// str(round(x)) of a float (no number where Python's round raises).
fn rounded(value: f64) -> Option<String> {
    value
        .is_finite()
        .then(|| format!("{:.0}", lp_py::round(value) + 0.0))
}

/// f"{x:.1f}"
fn fixed(value: f64) -> String {
    match value.is_nan() {
        true => "nan".into(),
        false => format!("{value:.1}"),
    }
}

pub fn whole(value: &Value) -> Option<String> {
    match value {
        Value::Bool(on) => Some(u8::from(*on).to_string()),
        Value::Int(_) => Some(value.str()),
        Value::Float(x) => rounded(*x),
        _ => None,
    }
}

pub fn tenth(value: &Value) -> Option<String> {
    number(value).map(fixed)
}

/// kilo: bytes a second as the display's KB/s: a tenth below 100.
pub fn kilo(value: &Value) -> Option<String> {
    let rate = number(value)? / 1024.0;
    match rate < 100.0 {
        true => Some(fixed(rate)),
        false => rounded(rate),
    }
}

pub fn text(value: &Value) -> Option<String> {
    match value {
        Value::Str(text) if !text.is_empty() => Some(text.clone()),
        _ => None,
    }
}

type How = fn(&Value) -> Option<String>;

/// The display's keys for Libre Panel's readings: (its English key, its Chinese key, the
/// reading, how it is written, the unit Waveshare's program puts after it). "Cpu_Freq" is the
/// processor's load: so Waveshare named it.
pub const KEYS: [(&str, &str, &str, How, &str); 21] = [
    ("Cpu_Freq", "Cpu使用率", "cpu.load", whole, "%"),
    ("Cpu_Temp", "Cpu温度", "cpu.temp", whole, "℃"),
    ("Cpu_Clock", "Cpu频率", "cpu.freq", whole, "MHZ"),
    ("Cpu_Power", "Cpu功耗", "cpu.power", whole, "W"),
    ("Cpu_Model", "Cpu型号", "cpu.name", text, ""),
    ("Gpu_Load", "显卡利用率", "gpu.load", whole, "%"),
    ("GPU_Temp", "显卡温度", "gpu.temp", whole, "℃"),
    ("Gpu_Clock", "显卡频率", "gpu.freq", whole, "MHZ"),
    ("Gpu_Power", "显卡功耗", "gpu.power", whole, "W"),
    ("Gpu_Fan", "显卡风扇", "gpu.fan", whole, "RPM"),
    ("Gpu_Usedata", "已用显存", "gpu.mem.used", whole, "MB"),
    ("Gpu_Model", "显卡型号", "gpu.name", text, ""),
    ("Memory_Load", "内存利用率", "mem.load", whole, "%"),
    ("Memory_Used", "已用内存", "mem.used", tenth, "GB"),
    ("Memory_Available", "可用内存", "mem.free", tenth, "GB"),
    ("RAM_Model_0", "内存型号_0", "mem.name", text, ""),
    ("Download_Speed", "下载速度", "net.down", kilo, "KB/s"),
    ("Upload_Speed", "上传速度", "net.up", kilo, "KB/s"),
    ("vol", "音量", "audio.volume", whole, ""),
    ("temperature", "气温", "weather.temperature", whole, "℃"),
    ("weather", "天气", "weather.description", text, ""),
];
/// Fans by what their name says (the sources name them by chip and header: fan.board.cpu_fan,
/// fan.lpc.pump): the display's key, its Chinese key, and the word looked for
pub const FANS: [(&str, &str, &str); 2] = [
    ("Cpu_Fan", "Cpu风扇", "cpu"),
    ("Pump_Fan", "水冷风扇", "pump"),
];
/// the drives' use: Libre Panel's disk.1 to disk.8, the display's 0 to 7
pub const PARTITIONS: usize = 8;
/// the clock as Waveshare's program writes it (the weekday short, in the app's language
/// here): the display's key, its Chinese key where it has one, and the format
pub const CLOCK: [(&str, &str, &str); 9] = [
    ("time", "时间", "%H:%M:%S"),
    ("date", "日期", "%Y-%m-%d"),
    ("week", "星期", "%a"),
    ("year", "", "%Y"),
    ("month", "", "%m"),
    ("day", "", "%d"),
    ("hour", "时", "%H"),
    ("minute", "分", "%M"),
    ("second", "秒", "%S"),
];

/// The readings the service reads.
pub fn wanted() -> Vec<String> {
    let mut keys: Vec<String> = KEYS.iter().map(|key| key.2.to_string()).collect();
    keys.extend(["fan.*".to_string(), "disk.load".to_string()]);
    keys.extend((1..=PARTITIONS).map(|n| format!("disk.{n}.load")));
    keys
}

/// fan: the first fan whose reading's key has `word` in it (`fan.cpu` first), or None.
pub fn fan(snapshot: &Snapshot, word: &str) -> Value {
    let mut keys: Vec<&str> = snapshot.readings.keys().map(String::as_str).collect();
    keys.sort_unstable();
    let first = format!("fan.{word}");
    for key in std::iter::once(first.as_str()).chain(keys) {
        let fits = key.starts_with("fan.") && lp_py::lower(key).contains(word);
        if fits && !key.ends_with(".percent") {
            let value = snapshot.value(key);
            if number(&value).is_some() {
                return value;
            }
        }
    }
    Value::None
}

/// values: what the display gets from `snapshot` at `now`: each value under both of its
/// keys, and only those Libre Panel has; with `units`, each number with its unit.
pub fn values(
    snapshot: Option<&Snapshot>,
    now: &DateTime,
    units: bool,
) -> Result<IndexMap<String, String>, String> {
    let mut out = IndexMap::new();
    let mut put = |english: &str, chinese: &str, value: Option<String>, unit: &str| {
        let Some(mut value) = value else {
            return;
        };
        if units {
            value.push_str(unit);
        }
        out.insert(english.to_string(), value.clone());
        if !chinese.is_empty() {
            out.insert(chinese.to_string(), value);
        }
    };
    let read = |key: &str| snapshot.map_or(Value::None, |snapshot| snapshot.value(key));
    for (english, chinese, reading, how, unit) in KEYS {
        put(english, chinese, how(&read(reading)), unit);
    }
    for (english, chinese, word) in FANS {
        let turns = snapshot.and_then(|snapshot| whole(&fan(snapshot, word)));
        put(english, chinese, turns, "RPM");
    }
    for n in 1..=PARTITIONS {
        let mut load = read(&format!("disk.{n}.load"));
        if n == 1 && load == Value::None {
            load = read("disk.load");
        }
        let (english, chinese) = (
            format!("Partition_Usage_{}", n - 1),
            format!("分区利用率_{}", n - 1),
        );
        put(&english, &chinese, whole(&load), "%");
    }
    for (english, chinese, fmt) in CLOCK {
        let said = lp_i18n::format_date(now, fmt).map_err(|why| format!("{why:?}"))?;
        put(english, chinese, Some(said), "");
    }
    Ok(out)
}

/// kind_of: what stands in the way, for the card: `busy` (another program has the port),
/// `denied` (no right to it), `silent` (a port that does not answer as the display does),
/// else `absent`.
pub fn kind_of(reason: &str) -> &'static str {
    let lowered = lp_py::lower(reason);
    let has = |what: &str| lowered.contains(what);
    if has("access is denied") || has("busy") || has("resource temporarily") {
        "busy"
    } else if has("permission denied") {
        "denied"
    } else if has("did not answer") {
        "silent"
    } else {
        "absent"
    }
}

/// What the service was set to.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub port: String,
    pub brightness: i64,
    /// (None: as it is)
    pub flip: Option<bool>,
    pub units: bool,
    pub every: f64,
    pub theme: String,
    pub rotate: Vec<String>,
    pub rotate_s: f64,
}

impl Settings {
    pub fn of(options: &Dict) -> Settings {
        let text = |key: &str| {
            let text = options.get(key).map(Value::str).unwrap_or_default();
            lp_py::strip(&text).to_string()
        };
        let number = |key: &str| options.get(key).and_then(Value::to_f64).unwrap_or(0.0);
        let rotate = match options.get("rotate") {
            Some(Value::List(paths)) => paths.iter().map(Value::str).collect(),
            _ => Vec::new(),
        };
        Settings {
            port: text("port"),
            brightness: (number("brightness") as i64).clamp(0, 100),
            flip: match text("flip").as_str() {
                "normal" => Some(false),
                "upside-down" => Some(true),
                _ => None,
            },
            units: options.get("units").is_some_and(Value::truthy),
            every: number("every_s").max(0.25),
            theme: text("theme"),
            rotate: (rotate.into_iter())
                .filter(|path: &String| !lp_py::strip(path).is_empty())
                .collect(),
            rotate_s: number("rotate_min").max(0.0) * 60.0,
        }
    }
}

/// What is round a display that is fed: the readings, the time, the choice of the next
/// theme, the clock, whether to end, the wait between two rounds and the log.
pub struct Around<'a> {
    pub snapshot: &'a dyn Fn() -> Option<Arc<Snapshot>>,
    pub now: &'a dyn Fn() -> DateTime,
    /// (the index of one of so many)
    pub pick: &'a dyn Fn(usize) -> usize,
    pub clock: &'a dyn Fn() -> f64,
    pub ended: &'a dyn Fn() -> bool,
    pub wait: &'a dyn Fn(f64),
    pub note: &'a dyn Fn(&str),
}

/// What the service says of itself (`summary`), and the display fed under its settings.
pub struct Feeder {
    pub settings: Settings,
    state: Mutex<Dict>,
}

fn told(pairs: &[(&str, Value)]) -> Dict {
    let pairs = pairs.iter().cloned();
    pairs.map(|(key, value)| (key.to_string(), value)).collect()
}

impl Feeder {
    pub fn new(settings: Settings) -> Feeder {
        Feeder {
            settings,
            state: Mutex::new(told(&[("state", Value::Str("searching".into()))])),
        }
    }

    /// summary: `state`: `connected` (with `port`, `inch`, `size` and `version` where the
    /// display says them, its `themes` as `{id, name}` and the `theme` this service last
    /// showed), `searching`, `absent`, `busy`, `denied` or `silent` (with the `reason`).
    pub fn summary(&self) -> Dict {
        self.state.lock().map(|s| s.clone()).unwrap_or_default()
    }

    pub fn say(&self, state: &[(&str, Value)]) {
        if let Ok(mut held) = self.state.lock() {
            *held = told(state);
        }
    }

    fn add(&self, fields: &[(&str, Value)]) {
        if let Ok(mut held) = self.state.lock() {
            held.extend(told(fields));
        }
    }

    /// _show: a theme on the card on the display; the picture turned again as set (each
    /// theme brings its own orientation, which the display takes on as it loads it).
    fn show(&self, display: &mut Display, path: &str, around: &Around) -> Result<(), DeviceError> {
        display.show_theme(path)?;
        if let Some(flipped) = self.settings.flip {
            display.set_flipped(flipped)?;
        }
        self.add(&[("theme", Value::Str(path.to_string()))]);
        let name = waveshare::theme_name(path);
        (around.note)(&format!("Waveshare USB monitor: theme {name}"));
        Ok(())
    }

    /// _feed: the display on `port` set up and given the readings until the service ends; an
    /// error when the display is gone.
    pub fn feed(
        &self,
        display: &mut Display,
        port: &str,
        about: &Told,
        around: &Around,
    ) -> Result<(), DeviceError> {
        // (the handshake was answered: values may still be taken)
        let version = display.version().unwrap_or_default();
        let said = |key: &str| about.get(key).cloned().flatten().unwrap_or_default();
        let (width, height) = (said("screen_w"), said("screen_h"));
        let size = match width.is_empty() || height.is_empty() {
            true => String::new(),
            false => format!("{width}×{height}"),
        };
        self.say(&[
            ("state", Value::Str("connected".into())),
            ("port", Value::Str(port.to_string())),
            ("inch", Value::Str(said("screen_inch"))),
            ("size", Value::Str(size)),
            ("version", Value::Str(version.clone())),
        ]);
        let named = match version.is_empty() {
            true => "version not said",
            false => &version,
        };
        (around.note)(&format!("Waveshare USB monitor on {port} ({named})"));
        if self.settings.brightness != 0 {
            display.set_brightness(self.settings.brightness)?;
        }
        if let Some(flipped) = self.settings.flip {
            display.set_flipped(flipped)?;
        }
        // (an older firmware may not tell: the values go on all the same)
        let themes = display.themes().unwrap_or_default();
        let listed = themes.iter().map(|path| {
            Value::Dict(told(&[
                ("id", Value::Str(path.clone())),
                ("name", Value::Str(waveshare::theme_name(path).to_string())),
            ]))
        });
        self.add(&[
            ("themes", Value::List(listed.collect())),
            ("theme", Value::Str(String::new())),
        ]);
        // a theme named that is not on the card (any more) is passed over
        let known = |path: &String| themes.is_empty() || themes.contains(path);
        if !self.settings.theme.is_empty() && known(&self.settings.theme) {
            self.show(display, &self.settings.theme, around)?;
        }
        let turns: Vec<&String> = self.settings.rotate.iter().filter(|p| known(p)).collect();
        let rotate_s = self.settings.rotate_s;
        let mut next_turn = (around.clock)() + rotate_s;
        while !(around.ended)() {
            let began = (around.clock)();
            if rotate_s != 0.0 && !turns.is_empty() && began >= next_turn {
                let current = match self.summary().get("theme") {
                    Some(Value::Str(path)) => path.clone(),
                    _ => String::new(),
                };
                let mut others: Vec<&String> =
                    turns.iter().copied().filter(|p| **p != current).collect();
                if others.is_empty() {
                    others.clone_from(&turns);
                }
                let chosen = others[(around.pick)(others.len()).min(others.len() - 1)].clone();
                self.show(display, &chosen, around)?;
                next_turn = began + rotate_s;
            }
            let snapshot = (around.snapshot)();
            let sent = values(snapshot.as_deref(), &(around.now)(), self.settings.units)
                .map_err(DeviceError)?;
            display.send_values(&sent)?;
            (around.wait)((self.settings.every - ((around.clock)() - began)).max(0.0));
        }
        Ok(())
    }
}

// -- the service --------------------------------------------------------------------------------

type Connect = Box<dyn Fn(&str) -> Result<(Display, String, Told), DeviceError> + Send + Sync>;

struct Shared {
    feeder: Feeder,
    host: ServiceHost,
    connect: Connect,
    began: Instant,
    ended: AtomicBool,
    wake: (Mutex<()>, Condvar),
}

/// The local time of now.
fn now() -> DateTime {
    let since = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64());
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    lp_py::zones::fromtimestamp(zone, since).map_or(DateTime::date(1970, 1, 1), |(at, _)| at)
}

/// random.choice's part: one of so many, by the system's random bytes.
fn pick(count: usize) -> usize {
    let mut bytes = [0u8; 8];
    let _ = getrandom::getrandom(&mut bytes);
    (u64::from_le_bytes(bytes) % count.max(1) as u64) as usize
}

impl Shared {
    fn ended(&self) -> bool {
        self.ended.load(Ordering::Relaxed)
    }

    fn wait(&self, seconds: f64) {
        if let Ok(asleep) = self.wake.0.lock() {
            let wait = Duration::from_secs_f64(seconds);
            let _woken = (self.wake.1).wait_timeout_while(asleep, wait, |()| !self.ended());
        }
    }

    fn run(&self) {
        let mut said_why = String::new();
        while !self.ended() {
            let (mut display, port, about) = match (self.connect)(&self.feeder.settings.port) {
                Ok(found) => found,
                Err(DeviceError(reason)) => {
                    let kind = kind_of(&reason);
                    self.feeder.say(&[
                        ("state", Value::Str(kind.into())),
                        ("reason", Value::Str(reason.clone())),
                    ]);
                    if reason != said_why {
                        self.host.note(&format!("Waveshare USB monitor: {reason}"));
                        said_why = reason;
                    }
                    self.wait(if kind == "silent" { QUIET_S } else { RETRY_S });
                    continue;
                }
            };
            said_why.clear();
            // (a panel of Libre Panel has it after all)
            if !take_place(&port) {
                display.close();
                self.wait(RETRY_S);
                continue;
            }
            let around = Around {
                snapshot: &|| self.host.snapshot(),
                now: &now,
                pick: &pick,
                clock: &|| self.began.elapsed().as_secs_f64(),
                ended: &|| self.ended(),
                wait: &|seconds| self.wait(seconds),
                note: &|text| self.host.note(text),
            };
            if let Err(DeviceError(why)) = self.feeder.feed(&mut display, &port, &about, &around) {
                (self.host).note(&format!("Waveshare USB monitor on {port}: {why}"));
                self.feeder.say(&[
                    ("state", Value::Str("absent".into())),
                    ("reason", Value::Str(why)),
                ]);
            }
            display.close();
            leave_place(&port);
            self.wait(1.0);
        }
    }
}

/// Libre Panel's readings in the themes of a Waveshare USB monitor.
pub struct Waveshare {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    vec![
        ("port", "string", Value::Str(String::new())),
        ("brightness", "int", Value::int(0)),
        ("flip", "string", Value::Str(String::new())),
        ("units", "bool", Value::Bool(false)),
        ("every_s", "number", Value::Float(1.0)),
        ("theme", "string", Value::Str(String::new())),
        ("rotate", "list", Value::List(Vec::new())),
        ("rotate_min", "number", Value::Float(0.0)),
    ]
}

/// The Waveshare display as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let connect: Connect = Box::new(|port| waveshare::connect(port, &waveshare::open_port));
            Ok(Box::new(Waveshare {
                shared: Arc::new(Shared {
                    feeder: Feeder::new(Settings::of(&options)),
                    host,
                    connect,
                    began: Instant::now(),
                    ended: AtomicBool::new(false),
                    wake: (Mutex::new(()), Condvar::new()),
                }),
                thread: None,
            }))
        },
    }
}

impl Service for Waveshare {
    fn start(&mut self) -> Result<(), String> {
        let keys = wanted();
        let keys: Vec<&str> = keys.iter().map(String::as_str).collect();
        self.shared.host.want(&keys);
        self.shared.ended.store(false, Ordering::Relaxed);
        let shared = Arc::clone(&self.shared);
        let thread = std::thread::Builder::new().name("waveshare".into());
        self.thread = Some(
            thread
                .spawn(move || shared.run())
                .map_err(|why| format!("the Waveshare display's thread did not start: {why}"))?,
        );
        Ok(())
    }

    fn stop(&mut self) {
        self.shared.ended.store(true, Ordering::Relaxed);
        if let Ok(_asleep) = self.shared.wake.0.lock() {
            self.shared.wake.1.notify_all();
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }

    fn summary(&self) -> Option<Value> {
        Some(Value::Dict(self.shared.feeder.summary()))
    }
}

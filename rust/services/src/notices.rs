//! Notices: short messages for what happens around the panel (services/notices.py).
//!
//! The volume turning, a new track, the microphone muted or on again, an appointment that
//! begins soon, a sensor that crosses a limit, the panel that came back from a restart. Each
//! one is a toast with a kind, a key, a rank and a payload, so the theme's toast style draws
//! it and the theme's `toast.off` and the screen's `suppresses` decide whether it shows. The
//! service only decides *when* something is worth a message.
//!
//! It looks at every new sample and asks for the readings it needs, so the volume and what
//! is playing are read although no theme on the panel shows them; both are also looked at
//! whenever they change between two samples (`fresh`).
//!
//! A sensor at its `crit` limit is also an alarm: the reading `alarm` is 1 (and `alarm.text`
//! says which, as its warning does) until it has been below the limit for `ALARM_HOLD_S`,
//! else 0.

use std::collections::{BTreeSet, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use lp_py::{Dict, Value};
use lp_render::overlays::Toast;
use lp_render::snapshot::Snapshot;

use crate::host::{Event, ServiceHost};
use crate::manager::{Part, Schema, Service};
use crate::power::number;

/// percent: a smaller change is no news
const VOLUME_STEP: f64 = 1.0;
const VOLUME_KEYS: [&str; 2] = ["audio.muted", "audio.volume"];
const MIC_KEY: &str = "audio.mic.muted";
/// the next ones looked at for a notice before they begin
const APPOINTMENTS: usize = 3;
/// without a volume reading for this long, say what is missing
const HINT_AFTER_S: f64 = 30.0;
const COVER: &str = "media.cover";
/// the icon of the program that plays, if a source brings it
const APP_ICON: &str = "media.app_icon";
/// a cover that comes after the title still joins the message
const COVER_WAIT_S: f64 = 3.0;
/// the alarm stays this long after the last value at a crit limit
pub const ALARM_HOLD_S: f64 = 10.0;

/// What a theme can switch off (`toast.off`), how it ranks, and how long it stays (None:
/// the theme's hold time).
struct Kind {
    name: &'static str,
    rank: i64,
    seconds: Option<f64>,
}

const VOLUME: Kind = Kind {
    name: "volume",
    rank: 0,
    seconds: None,
};
const MUSIC: Kind = Kind {
    name: "music",
    rank: 1,
    seconds: Some(5.0),
};
const MICROPHONE: Kind = Kind {
    name: "microphone",
    rank: 2,
    seconds: Some(3.0),
};
const APPOINTMENT: Kind = Kind {
    name: "appointment",
    rank: 2,
    seconds: Some(10.0),
};
const WARNING: Kind = Kind {
    name: "warning",
    rank: 2,
    seconds: Some(5.0),
};
/// (long enough for the first full frame after a restart)
const PANEL: Kind = Kind {
    name: "panel",
    rank: 2,
    seconds: Some(8.0),
};

fn icon_of(key: &str) -> Option<&'static str> {
    Some(match key.split('.').next()? {
        "cpu" => "cpu",
        "gpu" => "gpu",
        "ram" | "mem" => "ram",
        "disk" => "disk",
        "fan" => "fan",
        _ => return None,
    })
}

fn fixed(value: f64, digits: usize) -> String {
    lp_py::format::format(&Value::Float(value), &format!(".{digits}f")).unwrap_or_default()
}

/// readable: a value as it reads on a panel: 92, 4.2.
pub fn readable(value: f64) -> String {
    let whole = value.abs() >= 10.0 || value == lp_py::round(value);
    fixed(value, usize::from(!whole))
}

/// is_playing: whether something plays: `media.state` says "Playing" (else `media.playing`
/// is 1).
pub fn is_playing(snapshot: &Snapshot) -> bool {
    match snapshot.value("media.state") {
        Value::Str(state) => lp_py::lower(&state) == "playing",
        _ => snapshot.value("media.playing").truthy(),
    }
}

/// A translation with its placeholders filled; the message itself where they do not fit.
fn worded(text: &str, values: &[(&str, Value)]) -> String {
    lp_i18n::t_format(text, values).unwrap_or_else(|_| lp_i18n::t(text))
}

/// A sensor key and the values at which it is worth a message.
#[derive(Clone, Debug, PartialEq)]
pub struct Limit {
    pub key: String,
    pub warn: Option<f64>,
    pub crit: Option<f64>,
    pub name: String,
    pub icon: Option<String>,
    /// the value a full bar stands for
    pub scale: Option<f64>,
    /// the highest level told since the value was last below `rearm`
    shown: u8,
    /// when the last message was sent
    told: f64,
}

impl Limit {
    /// Below this the key is armed again.
    fn rearm(&self) -> f64 {
        match (self.warn, self.crit) {
            (Some(warn), Some(crit)) => warn.min(crit),
            (warn, crit) => warn.or(crit).unwrap_or(f64::INFINITY),
        }
    }

    fn level(&self, value: f64) -> u8 {
        if self.crit.is_some_and(|crit| value >= crit) {
            return 2;
        }
        u8::from(self.warn.is_some_and(|warn| value >= warn))
    }
}

/// parse_limit: one entry of the `warnings` option; a wrong one is reported and left out.
pub fn parse_limit(entry: &Value, place: &str, report: &mut dyn FnMut(String)) -> Option<Limit> {
    let table = entry.as_dict();
    let key = match table.and_then(|table| table.get("key")) {
        Some(Value::Str(key)) if !key.is_empty() => key,
        _ => {
            report(format!(
                "{place}: needs a sensor key, e.g. {{ key = 'cpu.temp', crit = 90 }}"
            ));
            return None;
        }
    };
    let table = table?;
    let mut found = [None; 3];
    for (n, field) in ["warn", "crit", "max"].into_iter().enumerate() {
        let given = table
            .get(field)
            .filter(|given| !matches!(given, Value::None));
        found[n] = number(given);
        if let (Some(given), None) = (given, found[n]) {
            report(format!(
                "{place}.{field}: expected a number, got {}",
                given.repr()
            ));
            return None;
        }
    }
    let [warn, crit, scale] = found;
    match (warn, crit) {
        (None, None) => {
            report(format!("{place}: needs a warn or a crit limit"));
            return None;
        }
        (Some(warn), Some(crit)) if warn >= crit => {
            report(format!("{place}: warn must be below crit"));
            return None;
        }
        _ => {}
    }
    Some(Limit {
        key: lp_py::strip(key).to_string(),
        warn,
        crit,
        name: match table.get("name") {
            Some(name) if name.truthy() => name.str(),
            _ => String::new(),
        },
        icon: match table.get("icon") {
            Some(Value::Str(icon)) => Some(icon.clone()),
            _ => None,
        },
        scale,
        shown: 0,
        told: f64::NEG_INFINITY,
    })
}

/// What the service's options say.
#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    pub volume: bool,
    /// "": the theme's toast style; else one of the volume's looks
    pub volume_look: String,
    pub music: bool,
    pub microphone: bool,
    /// minutes before an appointment begins; 0: none
    pub appointments: f64,
    pub restarted: bool,
    pub warnings: Vec<Value>,
    pub quiet_s: f64,
    /// a crit limit reached: on every panel (the "alarm" reading)
    pub alarm: bool,
}

impl Options {
    pub fn of(options: &Dict) -> Options {
        let on = |key: &str| options.get(key).is_none_or(Value::truthy);
        let amount = |key: &str, default: f64| {
            let given = options.get(key).and_then(Value::to_f64);
            given.unwrap_or(default)
        };
        Options {
            volume: on("volume"),
            volume_look: options
                .get("volume_look")
                .map(Value::str)
                .unwrap_or_default(),
            music: on("music"),
            microphone: on("microphone"),
            appointments: amount("appointments", 10.0),
            restarted: on("restarted"),
            warnings: match options.get("warnings") {
                Some(Value::List(warnings)) => warnings.clone(),
                _ => Vec::new(),
            },
            quiet_s: amount("quiet_s", 120.0),
            alarm: on("alarm"),
        }
    }
}

/// What a look at the readings asks of the host.
#[derive(Clone, Debug)]
pub enum Action {
    Tell(Box<Toast>),
    /// the reading `alarm` or `alarm.text`
    Publish(&'static str, Value),
    Unpublish(&'static str),
    /// a line for the log
    Note(String),
}

fn tell(
    kind: &Kind,
    text: String,
    payload: Dict,
    key: &str,
    level: &str,
    icon: Option<&str>,
) -> Action {
    Action::Tell(Box::new(Toast {
        icon: icon.map(str::to_string),
        level: level.to_string(),
        seconds: kind.seconds,
        kind: kind.name.to_string(),
        rank: kind.rank,
        payload,
        key: if key.is_empty() { kind.name } else { key }.to_string(),
        ..Toast::new(text)
    }))
}

fn labelled(label: &str) -> Dict {
    let mut payload = Dict::new();
    payload.insert("label".into(), Value::Str(lp_i18n::t(label)));
    payload
}

/// _limit_text: the name of the sensor, its value and its unit: "CPU temperature 92 °C".
fn limit_text(limit: &Limit, snapshot: &Snapshot, value: f64) -> String {
    let reading = snapshot.readings.get(&limit.key);
    let unit = reading.map_or("", |reading| reading.unit.as_str());
    let label = reading.map_or("", |reading| reading.label.as_str());
    let name = [limit.name.as_str(), label, limit.key.as_str()]
        .into_iter()
        .find(|name| !name.is_empty())
        .unwrap_or("");
    let text = format!("{name} {} {unit}", readable(value));
    text.trim_end_matches(lp_py::isspace).to_string()
}

/// When each message is due: what was seen at the last look.
#[derive(Clone, Debug)]
pub struct Core {
    options: Options,
    limits: Vec<Limit>,
    /// how the volume shows on every screen ("": as the theme's notices)
    look: String,
    /// at the last message, or the first value
    volume: Option<(f64, bool)>,
    volume_seen: bool,
    hinted: bool,
    /// (title, artist) of what plays
    track: Option<(String, String)>,
    /// told without a cover: until when one still joins the message
    late: Option<f64>,
    /// muted, as last seen
    mic: Option<bool>,
    /// (title, start) told already
    told_events: HashSet<(String, u64)>,
    began: f64,
    /// while the alarm is on: when it ends
    alarm_until: Option<f64>,
}

impl Core {
    /// The service of `options` at `now`; what is wrong with them is reported.
    pub fn new(options: Options, now: f64, report: &mut dyn FnMut(String)) -> Core {
        let limits = (options.warnings.iter().enumerate())
            .filter_map(|(n, entry)| parse_limit(entry, &format!("warnings[{}]", n + 1), report))
            .collect();
        let mut look = options.volume_look.clone();
        if !look.is_empty() && !lp_render::overlays::VOLUME_LOOKS.contains(&look.as_str()) {
            let mut names = lp_render::overlays::VOLUME_LOOKS.to_vec();
            names.sort_unstable();
            report(format!(
                "volume_look: {} is none of {}",
                lp_py::repr(&look),
                names.join(", ")
            ));
            look.clear();
        }
        Core {
            options,
            limits,
            look,
            volume: None,
            volume_seen: false,
            hinted: false,
            track: None,
            late: None,
            mic: None,
            told_events: HashSet::new(),
            began: now,
            alarm_until: None,
        }
    }

    pub fn options(&self) -> &Options {
        &self.options
    }

    /// The readings the service reads.
    pub fn wants(&self) -> Vec<String> {
        let mut keys: Vec<String> = self.limits.iter().map(|limit| limit.key.clone()).collect();
        if self.options.volume {
            keys.extend(VOLUME_KEYS.map(String::from));
        }
        if self.options.music {
            keys.push("media.*".into());
        }
        if self.options.microphone {
            keys.push(MIC_KEY.into());
        }
        if self.options.appointments > 0.0 {
            keys.extend((1..=APPOINTMENTS).map(|n| format!("calendar.{n}.*")));
        }
        keys
    }

    /// Whether changes between two samples are looked at.
    pub fn follows(&self) -> bool {
        self.options.volume || self.options.music || self.options.microphone
    }

    /// Whether the reading `alarm` is there from the start (a crit limit is set).
    pub fn alarms(&self) -> bool {
        self.options.alarm && self.limits.iter().any(|limit| limit.crit.is_some())
    }

    // -- every sample ------------------------------------------------------------------------

    pub fn sample(&mut self, snapshot: &Snapshot, now: f64) -> Vec<Action> {
        let mut out = Vec::new();
        if self.options.volume {
            self.check_volume(snapshot, now, &mut out);
        }
        if self.options.music {
            self.check_music(snapshot, now, &mut out);
        }
        if self.options.microphone {
            self.check_microphone(snapshot, &mut out);
        }
        if self.options.appointments > 0.0 {
            self.check_appointments(snapshot, &mut out);
        }
        for n in 0..self.limits.len() {
            self.check_limit(n, snapshot, now, &mut out);
        }
        if self.options.alarm {
            self.check_alarm(snapshot, now, &mut out);
        }
        out
    }

    /// The volume moved, or the track or its state changed, between two samples: tell it now,
    /// not with the next reading. (A track already told is not told again.)
    pub fn fresh(&mut self, snapshot: &Snapshot, keys: &BTreeSet<String>, now: f64) -> Vec<Action> {
        let mut out = Vec::new();
        if self.options.volume && VOLUME_KEYS.iter().any(|key| keys.contains(*key)) {
            self.check_volume(snapshot, now, &mut out);
        }
        if self.options.music && keys.iter().any(|key| key.starts_with("media.")) {
            self.check_music(snapshot, now, &mut out);
        }
        if self.options.microphone && keys.contains(MIC_KEY) {
            self.check_microphone(snapshot, &mut out);
        }
        out
    }

    fn check_microphone(&mut self, snapshot: &Snapshot, out: &mut Vec<Action>) {
        let Some(value) = number(Some(&snapshot.value(MIC_KEY))) else {
            return;
        };
        let muted = value != 0.0;
        let before = self.mic.replace(muted);
        if before.is_none_or(|before| before == muted) {
            return; // the first value is no change
        }
        let text = lp_i18n::t(if muted {
            "Microphone muted"
        } else {
            "Microphone on"
        });
        let mut payload = labelled("Microphone");
        payload.insert("muted".into(), Value::Bool(muted));
        let icon = if muted { "mic_off" } else { "mic" };
        out.push(tell(&MICROPHONE, text, payload, "", "info", Some(icon)));
    }

    /// An appointment that begins within `appointments` minutes, told once.
    fn check_appointments(&mut self, snapshot: &Snapshot, out: &mut Vec<Action>) {
        let lead = self.options.appointments;
        for n in 1..=APPOINTMENTS {
            let value = |field: &str| snapshot.value(&format!("calendar.{n}.{field}"));
            let (Value::Str(title), Some(minutes), Some(start)) = (
                value("title"),
                number(Some(&value("minutes"))),
                number(Some(&value("start"))),
            ) else {
                continue;
            };
            let clock = value("time");
            if matches!(&clock, Value::Str(clock) if *clock == lp_i18n::t("All day")) {
                continue; // a day, not a time to be somewhere
            }
            let event = (title.clone(), (start + 0.0).to_bits());
            if !(0.0 <= minutes && minutes <= lead) || !self.told_events.insert(event) {
                continue;
            }
            let named = ("title", Value::Str(title.clone()));
            let text = if minutes >= 0.5 {
                let whole = lp_py::round(minutes).max(1.0) as i64;
                let values = [("minutes", Value::int(whole)), named];
                worded("In {minutes} min: {title}", &values)
            } else {
                worded("Now: {title}", &[named])
            };
            let mut payload = labelled("Appointment");
            if let Value::Str(clock) = clock {
                if !clock.is_empty() {
                    let label = format!("{} · {clock}", lp_i18n::t("Appointment"));
                    payload.insert("label".into(), Value::Str(label));
                }
            }
            let key = format!("appointment:{title}:{}", fixed(start, 0));
            out.push(tell(
                &APPOINTMENT,
                text,
                payload,
                &key,
                "info",
                Some("calendar"),
            ));
        }
    }

    fn check_volume(&mut self, snapshot: &Snapshot, now: f64, out: &mut Vec<Action>) {
        let Some(volume) = number(Some(&snapshot.value("audio.volume"))) else {
            if !self.volume_seen && !self.hinted && now - self.began > HINT_AFTER_S {
                self.hinted = true;
                out.push(Action::Note(
                    "no volume readings: add \"audio\" to [sensors] providers in config.toml"
                        .into(),
                ));
            }
            return;
        };
        self.volume_seen = true;
        let muted = snapshot.value("audio.muted").truthy();
        let before = self.volume;
        if before.is_some_and(|(level, was)| muted == was && (volume - level).abs() < VOLUME_STEP) {
            return; // the mark stays what was told, so a slow turn still adds up to a step
        }
        self.volume = Some((volume, muted));
        if before.is_none() {
            return; // the first value is no change
        }
        let (text, share) = match muted {
            true => (lp_i18n::t("Muted"), 0.0),
            false => (fixed(volume, 0), volume / 100.0),
        };
        let mut payload = labelled("Volume");
        payload.insert("progress".into(), Value::Float(share));
        payload.insert("muted".into(), Value::Bool(muted));
        if !self.look.is_empty() {
            payload.insert("look".into(), Value::Str(self.look.clone()));
        }
        out.push(tell(&VOLUME, text, payload, "", "info", None));
    }

    fn check_music(&mut self, snapshot: &Snapshot, now: f64, out: &mut Vec<Action>) {
        let Value::Str(title) = snapshot.value("media.title") else {
            return;
        };
        if !is_playing(snapshot) {
            return; // paused: the track stays what it was, so resuming says nothing
        }
        let artist = match snapshot.value("media.artist") {
            Value::Str(artist) => artist,
            _ => String::new(),
        };
        let track = (title, artist);
        let cover = snapshot.images.contains_key(COVER);
        if self.track.as_ref() == Some(&track) {
            // told without a cover; has it come since?
            if let Some(until) = self.late {
                if now > until {
                    self.late = None;
                } else if cover {
                    self.late = None;
                    out.push(tell_track(snapshot, &track, cover));
                }
            }
            return;
        }
        let first = self.track.is_none();
        self.late = None;
        if !first {
            if !cover {
                self.late = Some(now + COVER_WAIT_S);
            }
            out.push(tell_track(snapshot, &track, cover));
        }
        // (what plays when the service starts is no news)
        self.track = Some(track);
    }

    fn check_limit(&mut self, n: usize, snapshot: &Snapshot, now: f64, out: &mut Vec<Action>) {
        let quiet = self.options.quiet_s;
        let limit = &mut self.limits[n];
        let Some(value) = number(Some(&snapshot.value(&limit.key))) else {
            return;
        };
        if value < limit.rearm() {
            limit.shown = 0;
            return;
        }
        let level = limit.level(value);
        if level <= limit.shown {
            return;
        }
        // (worse than what was told: news also in the quiet time)
        let escalation = limit.shown > 0;
        limit.shown = level;
        if !escalation && now - limit.told < quiet {
            return;
        }
        limit.told = now;
        let unit = (snapshot.readings.get(&limit.key)).map_or("", |reading| reading.unit.as_str());
        let mut payload = labelled("Warning");
        let full = (limit.scale.filter(|scale| *scale != 0.0)).or((unit == "%").then_some(100.0));
        if let Some(full) = full {
            let share = (value / full).clamp(0.0, 1.0);
            payload.insert("progress".into(), Value::Float(share));
        }
        let icon = (limit.icon.as_deref().filter(|icon| !icon.is_empty()))
            .or(icon_of(&limit.key))
            .or((unit == "°C").then_some("temperature"));
        let key = format!("warning:{}", limit.key);
        let level = if level == 2 { "error" } else { "warning" };
        let text = limit_text(limit, snapshot, value);
        out.push(tell(&WARNING, text, payload, &key, level, icon));
    }

    /// `alarm`: 1 while a sensor is at its crit limit, and `ALARM_HOLD_S` after.
    fn check_alarm(&mut self, snapshot: &Snapshot, now: f64, out: &mut Vec<Action>) {
        for limit in &self.limits {
            let value = number(Some(&snapshot.value(&limit.key)));
            let (Some(crit), Some(value)) = (limit.crit, value) else {
                continue;
            };
            if value < crit {
                continue;
            }
            let text = limit_text(limit, snapshot, value);
            if self.alarm_until.is_none() {
                out.push(Action::Note(format!("alarm: {text}")));
                out.push(Action::Publish("alarm", Value::int(1)));
            }
            out.push(Action::Publish("alarm.text", Value::Str(text)));
            self.alarm_until = Some(now + ALARM_HOLD_S);
            return;
        }
        if self.alarm_until.is_some_and(|until| now >= until) {
            self.alarm_until = None;
            out.push(Action::Publish("alarm", Value::int(0)));
            out.push(Action::Unpublish("alarm.text"));
        }
    }

    // -- the panel ---------------------------------------------------------------------------

    /// Back from a restart that cleared a hung video decoder (not from a replug), at `time`
    /// (the clock's hours and minutes).
    pub fn restarted(&self, time: &str) -> Action {
        let values = [("time", Value::Str(time.to_string()))];
        let text = worded("Panel restarted {time}", &values);
        tell(&PANEL, text, labelled("Panel"), "", "info", None)
    }
}

fn tell_track(snapshot: &Snapshot, track: &(String, String), cover: bool) -> Action {
    let (title, artist) = track;
    let mut payload = labelled("Now playing");
    // (the pictures by their keys: the layer takes them from the frame's)
    if cover {
        payload.insert("image".into(), Value::Str(COVER.into()));
    }
    if snapshot.images.contains_key(APP_ICON) {
        payload.insert("badge".into(), Value::Str(APP_ICON.into()));
    }
    if let Some(progress) = number(Some(&snapshot.value("media.progress"))) {
        payload.insert("progress".into(), Value::Float(progress));
    }
    let text = match artist.is_empty() {
        true => title.clone(),
        false => format!("{title} – {artist}"),
    };
    tell(&MUSIC, text, payload, "", "info", Some("music"))
}

struct Shared {
    core: Mutex<Core>,
    host: ServiceHost,
    began: Instant,
    /// (a sample that is under way then says nothing)
    stopped: AtomicBool,
}

impl Shared {
    fn act(&self, look: impl FnOnce(&mut Core, f64) -> Vec<Action>) {
        if self.stopped.load(Ordering::Relaxed) {
            return;
        }
        let now = self.began.elapsed().as_secs_f64();
        let actions = match self.core.lock() {
            Ok(mut core) => look(&mut core, now),
            Err(_) => return,
        };
        for action in actions {
            match action {
                Action::Tell(toast) => {
                    self.host.notify(*toast);
                }
                Action::Publish(key, value) => {
                    self.host.publish(key, value, "", &lp_i18n::t("Alarm"));
                }
                Action::Unpublish(key) => self.host.unpublish(key),
                Action::Note(line) => self.host.note(&line),
            }
        }
    }
}

/// The clock's hours and minutes now, as strftime("%H:%M") writes them.
fn clock_now() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64());
    lp_py::zones::local()
        .and_then(|zone| lp_py::zones::fromtimestamp(zone, now))
        .map(|(wall, _)| format!("{:02}:{:02}", wall.hour, wall.minute))
        .unwrap_or_default()
}

/// Messages for the volume, a new track, the microphone, an appointment soon, sensors past
/// a limit and a panel restart.
pub struct Notices {
    shared: Arc<Shared>,
}

fn options() -> Schema {
    vec![
        ("volume", "bool", Value::Bool(true)),
        ("volume_look", "string", Value::Str(String::new())),
        ("music", "bool", Value::Bool(true)),
        ("microphone", "bool", Value::Bool(true)),
        ("appointments", "int", Value::int(10)),
        ("restarted", "bool", Value::Bool(true)),
        ("warnings", "list", Value::List(Vec::new())),
        ("quiet_s", "number", Value::Float(120.0)),
        ("alarm", "bool", Value::Bool(true)),
    ]
}

/// The notices as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let mut report = |line: String| host.note(&line);
            let core = Core::new(Options::of(&options), 0.0, &mut report);
            Ok(Box::new(Notices {
                shared: Arc::new(Shared {
                    core: Mutex::new(core),
                    host,
                    began: Instant::now(),
                    stopped: AtomicBool::new(false),
                }),
            }))
        },
    }
}

impl Service for Notices {
    fn start(&mut self) -> Result<(), String> {
        let host = &self.shared.host;
        let (wants, follows, alarms, restarted) = match self.shared.core.lock() {
            Ok(core) => (
                core.wants(),
                core.follows(),
                core.alarms(),
                core.options().restarted,
            ),
            Err(_) => return Err("the notices cannot be started".into()),
        };
        let keys: Vec<&str> = wants.iter().map(String::as_str).collect();
        host.want(&keys);
        let shared = Arc::clone(&self.shared);
        host.on(
            "sample",
            Box::new(move |event| {
                if let Event::Sample { snapshot } = event {
                    shared.act(|core, now| core.sample(snapshot, now));
                }
            }),
        )?;
        if follows {
            let shared = Arc::clone(&self.shared);
            host.on(
                "fresh",
                Box::new(move |event| {
                    if let Event::Fresh { snapshot, keys } = event {
                        shared.act(|core, now| core.fresh(snapshot, keys, now));
                    }
                }),
            )?;
        }
        if restarted {
            let shared = Arc::clone(&self.shared);
            host.on(
                "panel-restarted",
                Box::new(move |_| shared.act(|core, _| vec![core.restarted(&clock_now())])),
            )?;
        }
        if alarms {
            host.publish("alarm", Value::int(0), "", &lp_i18n::t("Alarm"));
        }
        Ok(())
    }

    fn stop(&mut self) {
        self.shared.stopped.store(true, Ordering::Relaxed);
    }
}

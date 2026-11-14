//! What the app's Settings read and write, apart from HTTP (editor/settings.py).
//!
//! The routes in server.rs hand a request to [`Settings`] and turn its answer, or its
//! [`Refusal`], into JSON. Everything goes through config.toml (`edit_config`: the comments
//! stay, a change that would break the file is refused), so the running Libre Panel follows.

use std::path::Path;
use std::sync::{Arc, Mutex};

use lp_config::edit::Change;
use lp_config::{Config, DeviceConfig};
use lp_devices::models::PanelModel;
use lp_py::toml::{Table, Toml};
use lp_py::{repr, Dict, Value};
use num_bigint::BigInt;

use crate::background::BackgroundApp;
use crate::sensors::value_of;

pub const PROJECT_URL: &str = "https://github.com/syntensa/libre-panel";
const MAX_NOTICE: usize = 200_000;
/// The built-in services in the order the app lists them; plugins' come after them.
const SERVICE_ORDER: [&str; 11] = [
    "game",
    "memory",
    "notices",
    "autopilot",
    "devices",
    "cooling",
    "downloads",
    "timer",
    "power",
    "agents",
    "waveshare",
];
/// Sources that start by themselves; they are listed only where config.toml names them.
const IMPLICIT_SOURCES: [&str; 4] = ["media", "sky", "hardware", "mangohud"];
/// The sensors and the weather are made once when the panel loop starts.
const RESTART_PREFIXES: [&str; 2] = ["sensors.", "weather."];
/// What a setting names that Libre Panel starts, loads or writes: with administrator rights
/// these are not changed through the server.
const LOCKED_WHILE_ELEVATED: [&str; 4] = [
    "video.ffmpeg",
    "device.output",
    "sensors.presentmon.path",
    "sensors.librehardwaremonitor.dll_dir",
];
/// An option of a service or a source is a file or a folder when its name says so.
const PATH_WORDS: [&str; 13] = [
    "path",
    "dir",
    "folder",
    "file",
    "database",
    "fancontrol",
    "program",
    "exe",
    "command",
    "script",
    "dll",
    "library",
    "output",
];
/// The sensor sources of Libre Panel (sensors/base.py's), in its order.
pub(crate) const PROVIDERS: [&str; 18] = [
    "psutil",
    "linux",
    "librehardwaremonitor",
    "load",
    "audio",
    "demo",
    "media",
    "calendar",
    "homeassistant",
    "mqtt",
    "sky",
    "presentmon",
    "hardware",
    "mangohud",
    "rtss",
    "hwinfo",
    "json",
    "nut",
];
const DRIVERS: [&str; 3] = ["auto", "turzx", "virtual"]; // (sorted)
const LHM_MODES: [&str; 4] = ["auto", "service", "inprocess", "webserver"];
const LHM_URL: &str = "http://127.0.0.1:8085/data.json";
const BUILT_IN: &str = "built in";
const PLAIN_TRANSITIONS: [&str; 11] = [
    "cut",
    "fade",
    "slide",
    "slide-right",
    "slide-up",
    "slide-down",
    "wipe",
    "zoom",
    "iris",
    "blinds",
    "dissolve",
];
const ELABORATE_TRANSITIONS: [&str; 6] =
    ["tube", "entrance", "light", "glitch", "tiles", "shatter"];
const HOTKEYS: [&str; 8] = [
    "pause",
    "brightness_up",
    "brightness_down",
    "open",
    "next_scene",
    "timer",
    "timer_reset",
    "mic_mute",
];
/// What can be taken out of config.toml, so that its default holds (the shortcuts too).
const UNSETTABLE: [&str; 6] = [
    "refresh_ms",
    "weather.latitude",
    "weather.longitude",
    "weather.place",
    "device.at",
    "across",
];
const JSON_SOURCES: &str = "sensors.json.sources";
const JSON_MOST: usize = 16;
/// The options that are no more (config.py's GONE, the services' of them).
const GONE: [&str; 2] = ["services.agents.cloud", "services.agents.cloud_token"];

/// two writes at once would each start from the same file
static WRITING: Mutex<()> = Mutex::new(());

/// A request the settings refuse; `status` is the HTTP status that goes with it.
#[derive(Debug)]
pub struct Refusal {
    pub message: String,
    pub status: u16,
}

pub type Answer<T> = Result<T, Refusal>;

fn refusal(message: impl Into<String>, status: u16) -> Refusal {
    Refusal {
        message: message.into(),
        status,
    }
}

fn bad(message: impl Into<String>) -> Refusal {
    refusal(message, 400)
}

/// The written settings the running panel loop has not taken in yet: a new start of the loop
/// makes the sensors and the weather anew; until then a change to them waits.
#[derive(Default)]
pub struct Restart {
    /// the settings that wait, and the run of the loop they were written in (0: none ran)
    waiting: Mutex<(Vec<String>, u64)>,
}

impl Restart {
    fn written(&self, names: &[String], run: u64) {
        let wanted: Vec<&String> = names
            .iter()
            .filter(|name| RESTART_PREFIXES.iter().any(|p| name.starts_with(p)))
            .collect();
        if wanted.is_empty() {
            return;
        }
        self.pending(run); // a restart since the last change clears that one first
        if let Ok(mut waiting) = self.waiting.lock() {
            waiting.1 = run;
            for name in wanted {
                if !waiting.0.contains(name) {
                    waiting.0.push(name.clone());
                }
            }
        }
    }

    fn pending(&self, run: u64) -> Vec<String> {
        let Ok(mut waiting) = self.waiting.lock() else {
            return Vec::new();
        };
        if !waiting.0.is_empty() && waiting.1 != 0 && run != waiting.1 {
            waiting.0.clear();
        }
        waiting.0.clone()
    }
}

fn dict<const N: usize>(pairs: [(&str, Value); N]) -> Value {
    Value::Dict(
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

fn text(value: &str) -> Value {
    Value::Str(value.to_string())
}

fn texts<T: AsRef<str>>(values: &[T]) -> Value {
    Value::List(values.iter().map(|v| text(v.as_ref())).collect())
}

/// A cleaned value as config.toml holds it; None for what has no place there (null).
pub fn toml_of(value: &Value) -> Option<Toml> {
    Some(match value {
        Value::None => return None,
        Value::Bool(on) => Toml::Bool(*on),
        Value::Int(n) => Toml::Int(n.clone()),
        Value::Float(f) => Toml::Float(*f),
        Value::Str(text) => Toml::Str(text.clone()),
        Value::List(items) => Toml::Array(items.iter().map(toml_of).collect::<Option<_>>()?),
        Value::Dict(table) => Toml::Table(table_of(table)?),
    })
}

fn table_of(table: &Dict) -> Option<Table> {
    table
        .iter()
        .map(|(key, value)| Some((key.clone(), toml_of(value)?)))
        .collect()
}

// -- what a setting may be ----------------------------------------------------------------

fn whole(name: &str, value: &Value, low: i64, high: i64) -> Answer<Value> {
    let Value::Int(number) = value else {
        return Err(bad(format!("{name}: expected a whole number")));
    };
    if *number < BigInt::from(low) || *number > BigInt::from(high) {
        return Err(bad(format!("{name}: must be between {low} and {high}")));
    }
    Ok(value.clone())
}

fn number(name: &str, value: &Value, low: f64, high: f64) -> Answer<Value> {
    let given = match value {
        Value::Int(_) | Value::Float(_) => value.to_f64().filter(|f| f.is_finite()),
        _ => None,
    };
    let Some(given) = given else {
        return Err(bad(format!("{name}: expected a number")));
    };
    if !(low <= given && given <= high) {
        return Err(bad(format!(
            "{name}: must be between {} and {}",
            lp_py::format_g(low, 6),
            lp_py::format_g(high, 6)
        )));
    }
    Ok(Value::Float(given))
}

fn plain_text(name: &str, value: &Value, limit: usize, required: bool) -> Answer<String> {
    let Some(given) = value.as_str().filter(|text| !text.contains('\0')) else {
        return Err(bad(format!("{name}: expected text")));
    };
    let given = lp_py::strip(given);
    if required && given.is_empty() {
        return Err(bad(format!("{name}: must not be empty")));
    }
    if given.len() > limit {
        return Err(bad(format!("{name}: at most {limit} bytes")));
    }
    Ok(given.to_string())
}

fn a_text(name: &str, value: &Value, limit: usize) -> Answer<Value> {
    plain_text(name, value, limit, false).map(Value::Str)
}

/// As config.toml takes video.maxrate: "auto", or digits (with a fraction) and k, K or M.
fn rate(name: &str, value: &Value) -> Answer<Value> {
    let fits = |text: &str| {
        if text == "auto" {
            return true;
        }
        let digits = text.strip_suffix(['k', 'K', 'M']).unwrap_or(text);
        let (whole, fraction) = match digits.split_once('.') {
            Some((whole, fraction)) => (whole, Some(fraction)),
            None => (digits, None),
        };
        let all = |part: &str| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit());
        all(whole) && fraction.is_none_or(all)
    };
    match value.as_str().map(lp_py::strip) {
        Some(given) if fits(given) => Ok(text(given)),
        _ => Err(bad(format!(
            "{name}: \"auto\", or a bit rate such as \"2M\" or \"1500k\""
        ))),
    }
}

fn choice<T: AsRef<str>>(name: &str, value: &Value, allowed: &[T]) -> Answer<Value> {
    match value {
        Value::Str(given) if allowed.iter().any(|a| a.as_ref() == given) => Ok(value.clone()),
        _ => {
            let listed: Vec<&str> = allowed.iter().map(AsRef::as_ref).collect();
            Err(bad(format!("{name}: must be one of {}", listed.join(", "))))
        }
    }
}

fn choice_whole(name: &str, value: &Value, allowed: &[i64]) -> Answer<Value> {
    match value {
        Value::Int(given) if allowed.iter().any(|a| BigInt::from(*a) == *given) => {
            Ok(value.clone())
        }
        _ => {
            let listed: Vec<String> = allowed.iter().map(i64::to_string).collect();
            Err(bad(format!("{name}: must be one of {}", listed.join(", "))))
        }
    }
}

/// `"7:30"` as `"07:30"`, or `"sunset"` and `"sunrise"`; anything else is refused.
fn time_of_day(name: &str, value: &Value) -> Answer<Value> {
    match value.as_str().and_then(lp_config::night_time) {
        Some(time) => Ok(Value::Str(time)),
        None => Err(bad(format!(
            "{name}: a time of day such as \"22:00\", or \"sunset\""
        ))),
    }
}

fn flag(name: &str, value: &Value) -> Answer<Value> {
    match value {
        Value::Bool(_) => Ok(value.clone()),
        _ => Err(bad(format!("{name}: expected true or false"))),
    }
}

/// A service's option as its spec says (the types of apply_options).
fn option(name: &str, value: &Value, kind: &str) -> Answer<Value> {
    const MOST: i64 = 1 << 53;
    match kind {
        "int" => whole(name, value, -MOST, MOST),
        "number" => number(name, value, -1e18, 1e18),
        "bool" => flag(name, value),
        "string" => a_text(name, value, 500),
        "list" => {
            let scalar = |item: &Value| match item {
                Value::Str(_) | Value::Int(_) | Value::Bool(_) => true,
                Value::Float(f) => f.is_finite(),
                _ => false,
            };
            match value {
                Value::List(items) if items.len() <= 200 && items.iter().all(scalar) => {
                    Ok(value.clone())
                }
                _ => Err(bad(format!("{name}: expected a list of texts or numbers"))),
            }
        }
        _ => Err(bad(format!(
            "{name}: an option of a kind the settings do not know ({kind})"
        ))),
    }
}

/// Whether the setting `name` names a program, a library, a file or a folder that Libre
/// Panel starts, loads, reads or writes.
pub fn names_a_path(name: &str) -> bool {
    if LOCKED_WHILE_ELEVATED.contains(&name) {
        return true;
    }
    let parts: Vec<&str> = name.split('.').collect();
    if parts.len() == 3 && (parts[0] == "sensors" || parts[0] == "services") {
        let option = parts[2].to_lowercase();
        return PATH_WORDS.iter().any(|word| option.contains(word));
    }
    false
}

fn refused_with_rights(name: &str) -> Refusal {
    let said = lp_i18n::t_format(
        "While Libre Panel runs with administrator rights, {name} is not changed here: it \
         names a program or a file that would then be started or written with those \
         rights. Set it in config.toml in your settings folder; Libre Panel reads it \
         from there.",
        &[("name", text(name))],
    );
    refusal(said.unwrap_or_else(|why| why.msg), 403)
}

/// What writing nothing (None) to config.toml comes to: Python's own error, as the app
/// shows it.
fn unwritable() -> Refusal {
    refusal("TypeError: cannot write NoneType to config.toml", 500)
}

/// `value` as JSON can say it: nan is nothing (dates are their texts already).
fn plain(value: &Value) -> Value {
    match value {
        Value::Dict(table) => Value::Dict(
            table
                .iter()
                .map(|(key, item)| (key.clone(), plain(item)))
                .collect(),
        ),
        Value::List(items) => Value::List(items.iter().map(plain).collect()),
        Value::Float(f) if !f.is_finite() => Value::None,
        other => other.clone(),
    }
}

/// A panel's settings, as `[device]` has them (but the video's, and `beside`).
fn device_table(device: &DeviceConfig) -> Dict {
    let at = match device.at {
        Some((x, y)) => Value::List(vec![Value::int(x), Value::int(y)]),
        None => Value::None,
    };
    let Value::Dict(table) = dict([
        ("model", text(&device.model)),
        ("driver", text(&device.driver)),
        ("brightness", Value::int(device.brightness)),
        ("rotate", Value::int(device.rotate)),
        ("orientation", text(&device.orientation)),
        ("motion", text(&device.motion)),
        ("still", text(&device.still)),
        ("video_range", text(&device.video_range)),
        ("jpeg", text(&device.jpeg)),
        ("jpeg_quality", Value::int(device.jpeg_quality)),
        ("dark_on_quit", Value::Bool(device.dark_on_quit)),
        ("dark_on_sleep", Value::Bool(device.dark_on_sleep)),
        ("night", Value::Bool(device.night)),
        ("night_from", text(&device.night_from)),
        ("night_to", text(&device.night_to)),
        ("night_brightness", Value::int(device.night_brightness)),
        ("dark_when_away", Value::Bool(device.dark_when_away)),
        ("away_brightness", Value::int(device.away_brightness)),
        ("output", text(&device.output)),
        ("port", text(&device.port)),
        ("at", at),
    ]) else {
        unreachable!()
    };
    table
}

/// What a panel beside the first, `[[panels]]` at `n`, has by default.
fn panel_device(n: usize) -> DeviceConfig {
    DeviceConfig {
        driver: "turzx".into(),
        output: format!("libre-panel-frame-{}.png", n + 2),
        ..DeviceConfig::default()
    }
}

/// `[sensors.json]` for the app: how often, and each source as the form edits it. The
/// headers (a token, a password) are not handed out: the form says whether there are some.
fn json_sources(config: &Config) -> Value {
    let options = config.sensors.options.get("json");
    let mut sources = Vec::new();
    let listed = options.and_then(|options| options.get("sources"));
    if let Some(Toml::Array(entries)) = listed {
        for entry in entries {
            let Toml::Table(entry) = entry else {
                continue;
            };
            let of = |key: &str| entry.get(key).map(value_of).filter(Value::truthy);
            let said =
                |value: Option<Value>| value.map_or(String::new(), |v| lp_py::format::str_of(&v));
            let pairs = |key: &str| match entry.get(key) {
                Some(Toml::Table(pairs)) => Value::Dict(
                    pairs
                        .iter()
                        .map(|(k, v)| (k.clone(), Value::Str(lp_py::format::str_of(&value_of(v)))))
                        .collect(),
                ),
                _ => Value::Dict(Dict::default()),
            };
            sources.push(dict([
                ("name", Value::Str(said(of("name")))),
                ("where", Value::Str(said(of("url").or_else(|| of("file"))))),
                ("fields", pairs("fields")),
                ("units", pairs("units")),
                ("headers", Value::Bool(of("headers").is_some())),
            ]));
        }
    }
    let every = match options.and_then(|options| options.get("update_s")) {
        Some(every @ (Toml::Int(_) | Toml::Float(_) | Toml::Bool(_))) => value_of(every),
        _ => Value::int(30),
    };
    dict([
        ("update_s", plain(&every)),
        ("sources", Value::List(sources)),
    ])
}

fn transition_list() -> Vec<(&'static str, &'static str)> {
    let plain = PLAIN_TRANSITIONS.iter().map(|name| (*name, "plain"));
    let elaborate = ELABORATE_TRANSITIONS
        .iter()
        .map(|name| (*name, "elaborate"));
    plain.chain(elaborate).collect()
}

fn known_transitions() -> Vec<&'static str> {
    transition_list()
        .into_iter()
        .map(|(name, _)| name)
        .collect()
}

/// Whether the program is a download's (not one built in its source tree): what Python
/// calls frozen.
pub fn packaged() -> bool {
    let Ok(program) = std::env::current_exe() else {
        return false;
    };
    let folder = program.parent().and_then(Path::file_name);
    let above = program
        .parent()
        .and_then(Path::parent)
        .and_then(Path::file_name);
    let built = matches!(folder.and_then(|f| f.to_str()), Some("release" | "debug"))
        && above.and_then(|f| f.to_str()) == Some("target");
    !built
}

/// Whether video.encoder "auto" takes Libre Panel's engine: the download carries it (its
/// `tools` folder).
fn carries_engine() -> bool {
    let name = match cfg!(windows) {
        true => "libre-panel-engine.exe",
        false => "libre-panel-engine",
    };
    packaged()
        && std::env::current_exe().ok().is_some_and(|program| {
            let program = std::fs::canonicalize(&program).unwrap_or(program);
            program.with_file_name("tools").join(name).is_file()
        })
}

/// cli_command: the command-line program as one types it in a terminal: where a download is
/// not on the PATH its own (the Windows download's, the AppImage file), else `libre-panel`.
pub fn cli_command(arguments: &str) -> String {
    if packaged() {
        if cfg!(windows) {
            if let Ok(program) = std::env::current_exe() {
                return format!("\"{}\" {arguments}", program.display());
            }
        }
        // the .AppImage file, not its temporary mount
        let appimage = std::env::var("APPIMAGE").unwrap_or_default();
        if !appimage.is_empty() && cfg!(target_os = "linux") {
            return format!("\"{appimage}\" {arguments}");
        }
    }
    format!("libre-panel {arguments}")
}

/// The folders the About page shows and opens: the settings, the user's themes, the log.
pub fn folders() -> [(&'static str, std::path::PathBuf); 3] {
    let log = lp_config::dirs::log_file();
    [
        ("config", lp_config::dirs::config_dir()),
        ("themes", lp_config::dirs::user_themes_dir()),
        (
            "logs",
            log.parent().map(Path::to_path_buf).unwrap_or_default(),
        ),
    ]
}

/// One request's view of the settings: config.toml, and the background app if there is one.
pub struct Settings<'a> {
    pub home: &'a crate::home::Home,
    pub config_file: &'a Path,
    pub controls: Option<Arc<BackgroundApp>>,
    pub restart: &'a Restart,
}

impl Settings<'_> {
    fn config(&self) -> Answer<Config> {
        lp_config::load_config(self.config_file, lp_config::platform())
            .map_err(|why| refusal(why.msg, 409))
    }

    /// The run of the panel loop (0: none).
    fn run(&self) -> u64 {
        self.controls.as_ref().map_or(0, |app| app.panel.run())
    }

    /// Have the running app take in what was written. Nothing happens in a plain editor.
    fn follow(&self) {
        if let Some(app) = &self.controls {
            app.follow_config();
        }
    }

    // -- the values -----------------------------------------------------------------------

    /// config.toml as the app needs it, with the defaults filled in, and what it has to know
    /// to offer choices (the sources, the drivers, the services and their options).
    pub fn read(&self) -> Answer<Value> {
        let config = self.config()?;
        let lhm = config.sensors.options.get("librehardwaremonitor");
        let mode = match lhm.and_then(|lhm| lhm.get("mode")) {
            Some(mode) => lp_py::format::str_of(&value_of(mode)).to_lowercase(),
            None => "auto".to_string(),
        };
        let mode = match LHM_MODES.contains(&mode.as_str()) {
            true => mode,
            false => "auto".to_string(),
        };
        let url = match lhm.and_then(|lhm| lhm.get("url")) {
            Some(Toml::Str(url)) if !url.is_empty() => url.clone(),
            _ => LHM_URL.to_string(),
        };
        let (device, video, weather) = (&config.device, &config.device.video, &config.weather);
        let services: Dict = (config.services.options.iter())
            .map(|(name, table)| {
                let kept = table
                    .iter()
                    .filter(|(key, _)| !GONE.contains(&format!("services.{name}.{key}").as_str()))
                    .map(|(key, value)| (key.clone(), plain(&value_of(value))))
                    .collect();
                (name.clone(), Value::Dict(kept))
            })
            .collect();
        let mut device_told = device_table(device);
        device_told.shift_remove("jpeg");
        let mut transitions = Dict::default();
        transitions.insert("default".into(), text(&config.transition));
        transitions.insert("speed".into(), text(&config.transitions.speed));
        for (occasion, name) in &config.transitions.occasions {
            transitions.insert(occasion.clone(), text(name));
        }
        transitions.insert("random".into(), texts(&config.transitions.random));
        let switches = (config.switches.iter())
            .map(|(theme, chosen)| {
                let chosen = chosen.iter().map(|(k, on)| (k.clone(), Value::Bool(*on)));
                (theme.clone(), Value::Dict(chosen.collect()))
            })
            .collect();
        let panels = (config.panels.iter())
            .map(|panel| {
                let Value::Dict(mut told) = dict([
                    ("theme", text(&panel.theme)),
                    ("on", Value::Bool(panel.on)),
                    ("follow_brightness", Value::Bool(panel.follow_brightness)),
                    ("brightness_offset", Value::int(panel.brightness_offset)),
                    ("game_theme", text(&panel.game_theme)),
                    ("media_theme", text(&panel.media_theme)),
                    ("alarm_theme", text(&panel.alarm_theme)),
                    ("same_look", Value::Bool(panel.same_look)),
                    ("follow", panel.follow.map_or(Value::None, Value::Bool)),
                    (
                        "video",
                        dict([
                            ("mode", text(&panel.video_mode)),
                            ("local_clip", text(&panel.local_clip)),
                            ("clip_asked", Value::Bool(panel.clip_asked)),
                        ]),
                    ),
                ]) else {
                    unreachable!()
                };
                told.extend(device_table(&panel.device));
                Value::Dict(told)
            })
            .collect();
        let whole_of = |n: &Option<BigInt>| n.clone().map_or(Value::None, Value::Int);
        let float_of = |f: Option<f64>| f.map_or(Value::None, Value::Float);
        let told = dict([
            ("device", Value::Dict(device_told)),
            (
                "video",
                dict([
                    ("mode", text(&video.mode)),
                    ("fps", Value::int(video.fps)),
                    ("device_fps", Value::int(video.device_fps)),
                    ("crf", Value::int(video.crf)),
                    ("preset", text(&video.preset)),
                    ("keyframe_s", Value::Float(video.keyframe_s)),
                    ("maxrate", text(&video.maxrate)),
                    ("profile", text(&video.profile)),
                    ("encoder", text(&video.encoder)),
                    ("engine_sends", Value::Bool(video.engine_sends)),
                    ("engine_layers", Value::Bool(video.engine_layers)),
                    ("engine_encoder", text(&video.engine_encoder)),
                    ("pixels", text(&video.pixels)),
                    ("direct", Value::Bool(video.direct)),
                    ("rest", Value::Bool(video.rest)),
                    ("local_clip", text(&video.local_clip)),
                    ("clip_asked", Value::Bool(video.clip_asked)),
                    ("ffmpeg", text(&video.ffmpeg)),
                ]),
            ),
            (
                "sensors",
                dict([
                    ("providers", texts(&config.sensors.providers)),
                    (
                        "librehardwaremonitor",
                        dict([("mode", Value::Str(mode)), ("url", Value::Str(url))]),
                    ),
                    ("json", json_sources(&config)),
                ]),
            ),
            ("refresh_ms", whole_of(&config.refresh_ms)),
            (
                "weather",
                dict([
                    ("enabled", Value::Bool(weather.enabled)),
                    ("latitude", float_of(weather.latitude)),
                    ("longitude", float_of(weather.longitude)),
                    ("place", text(&weather.place)),
                    ("units", text(&weather.units)),
                    ("update_minutes", Value::Int(weather.update_minutes.clone())),
                ]),
            ),
            (
                "services",
                dict([
                    ("enabled", texts(&config.services.enabled)),
                    ("options", Value::Dict(services)),
                ]),
            ),
            ("language", text(&config.language)),
            (
                "check_updates",
                config.check_updates.map_or(Value::None, Value::Bool),
            ),
            ("allow_notify", Value::Bool(config.allow_notify)),
            ("tray_icon", Value::Bool(config.tray_icon)),
            (
                "hotkeys",
                Value::Dict(
                    (config.hotkeys.iter())
                        .map(|(action, combo)| (action.clone(), text(combo)))
                        .collect(),
                ),
            ),
            ("hotkeys_enabled", Value::Bool(config.hotkeys_enabled)),
            ("symbols", text(&config.symbols)),
            ("cards", texts(&config.cards)),
            ("card_order", text(&config.card_order)),
            ("card_transition", text(&config.card_transition)),
            ("accent", text(&config.accent)),
            ("card_opacity", Value::int(config.card_opacity)),
            ("light_colours", Value::Bool(config.light_colours)),
            ("graph_steps", Value::Bool(config.graph_steps)),
            ("across", text(&config.across)),
            // how the panel goes from one screen to the next
            ("transitions", Value::Dict(transitions)),
            ("switches", Value::Dict(switches)),
            // the panels beside this one ([[panels]]): written back whole ("panels")
            ("panels", Value::List(panels)),
        ]);
        Ok(dict([
            ("config", told),
            ("catalog", self.catalog()),
            ("restart", texts(&self.restart.pending(self.run()))),
        ]))
    }

    fn catalog(&self) -> Value {
        let parts = crate::services::parts();
        let rank = |name: &str| {
            let place = SERVICE_ORDER.iter().position(|known| *known == name);
            place.unwrap_or(SERVICE_ORDER.len())
        };
        let plugins = crate::plugins::catalog(&self.home.settings).unwrap_or_default();
        let theirs = crate::plugins::entries(&plugins, "services");
        let mut names: Vec<&str> = parts.keys().copied().collect();
        for service in &theirs {
            if let Some(Value::Str(name)) = service.get("name") {
                names.push(name.as_str());
            }
        }
        names.sort_by(|a, b| (rank(a), *a).cmp(&(rank(b), *b)));
        let services = names
            .into_iter()
            .map(|name| {
                // (a plugin's, as the plugin host tells it)
                if let Some(service) = theirs.iter().find(|s| s.get("name") == Some(&text(name))) {
                    let got = |key: &str| service.get(key).cloned().unwrap_or(Value::None);
                    return dict([
                        ("name", text(name)),
                        ("source", got("source")),
                        ("builtin", Value::Bool(false)),
                        ("error", got("error")),
                        ("options", got("options")),
                    ]);
                }
                let options = (parts[name].options)()
                    .into_iter()
                    .map(|(key, kind, default)| {
                        dict([
                            ("key", text(key)),
                            ("type", text(kind)),
                            ("default", plain(&default)),
                        ])
                    })
                    .collect();
                dict([
                    ("name", text(name)),
                    ("source", text(BUILT_IN)),
                    ("builtin", Value::Bool(true)),
                    ("error", text("")),
                    ("options", Value::List(options)),
                ])
            })
            .collect();
        let mut providers: Vec<Value> = PROVIDERS
            .iter()
            .map(|name| {
                dict([
                    ("name", text(name)),
                    ("source", text(BUILT_IN)),
                    ("builtin", Value::Bool(true)),
                ])
            })
            .collect();
        for source in crate::plugins::entries(&plugins, "sensors") {
            let got = |key: &str| source.get(key).cloned().unwrap_or(Value::None);
            providers.push(dict([
                ("name", got("name")),
                ("source", got("source")),
                ("builtin", Value::Bool(false)),
            ]));
        }
        let transitions = transition_list()
            .into_iter()
            .map(|(id, group)| dict([("id", text(id)), ("group", text(group))]))
            .collect();
        let platform = lp_config::platform();
        dict([
            ("platform", text(platform)),
            ("engine", Value::Bool(carries_engine())),
            ("providers", Value::List(providers)),
            ("implicit_providers", texts(&IMPLICIT_SOURCES)),
            (
                "default_providers",
                texts(&lp_config::default_providers(platform)),
            ),
            ("lhm_modes", texts(&LHM_MODES)),
            ("drivers", texts(&DRIVERS)),
            ("services", Value::List(services)),
            ("transitions", Value::List(transitions)),
        ])
    }

    // -- writing --------------------------------------------------------------------------

    /// `{"set": {"device.brightness": 70, "refresh_ms": null}}`: any number of settings in
    /// one write to config.toml, each as `null` (to take it out, where that is a thing) or a
    /// value its rule accepts. Answers like [`Settings::read`].
    pub fn write(&self, payload: &Value) -> Answer<Value> {
        let changes = match payload {
            Value::Dict(payload) => match payload.get("set") {
                Some(Value::Dict(changes)) if !changes.is_empty() && changes.len() <= 60 => {
                    Some(changes)
                }
                _ => None,
            },
            _ => None,
        };
        let Some(changes) = changes else {
            return Err(bad("expected {\"set\": {\"device.brightness\": 70, ...}}"));
        };
        let names = {
            let _writing = WRITING.lock();
            let config = self.config()?;
            let mut edits = Vec::new();
            for (name, value) in changes {
                edits.push(self.check(name, value, &config)?);
            }
            check_together(changes, &config)?;
            // a panel's own theme: the scene is off
            if changes.contains_key("panels") && !config.scene.is_empty() {
                edits.push(Change::Unset("scene".into()));
            }
            lp_config::edit::edit_config(&edits, self.config_file, lp_config::platform())
                .map_err(|why| refusal(why.msg, 409))?;
            let named = |change: &Change| match change {
                Change::Value(name, _) | Change::Unset(name) | Change::Tables(name, _) => {
                    name.clone()
                }
            };
            edits.iter().map(named).collect::<Vec<String>>()
        };
        self.restart.written(&names, self.run());
        self.follow();
        self.read()
    }

    /// Refuse a change of what names a program or a file while Libre Panel runs with
    /// administrator rights: it would start or write it with them. What config.toml says
    /// already, said again, is no change.
    fn check_rights(
        &self,
        name: &str,
        value: &Value,
        config: &Config,
        default: &Value,
    ) -> Answer<()> {
        if !names_a_path(name) || !crate::access::elevated() {
            return Ok(());
        }
        let parts: Vec<&str> = name.split('.').collect();
        let current = match name {
            "video.ffmpeg" => text(&config.device.video.ffmpeg),
            "device.output" => text(&config.device.output),
            _ if parts.len() == 3 && (parts[0] == "sensors" || parts[0] == "services") => {
                let tables = match parts[0] {
                    "sensors" => &config.sensors.options,
                    _ => &config.services.options,
                };
                let found = tables.get(parts[1]).and_then(|table| table.get(parts[2]));
                found.map_or(Value::None, value_of)
            }
            _ => Value::None,
        };
        let before = if current == Value::None {
            default
        } else {
            &current
        };
        let after = if *value == Value::None {
            default
        } else {
            value
        };
        match after == before {
            true => Ok(()),
            false => Err(refused_with_rights(name)),
        }
    }

    /// The change for a setting, or the error.
    fn check(&self, name: &str, value: &Value, config: &Config) -> Answer<Change> {
        if name.starts_with("services.") {
            return self.check_option(name, value, config);
        }
        if name.starts_with("switches.") {
            return check_switch(name, value);
        }
        if name == "panels" {
            return Ok(Change::Tables(name.into(), check_panels(value, config)?));
        }
        if name == JSON_SOURCES {
            return Ok(Change::Tables(
                name.into(),
                check_json_sources(value, config)?,
            ));
        }
        self.check_rights(name, value, config, &Value::None)?;
        if *value == Value::None {
            let shortcut = name.strip_prefix("hotkeys.");
            let goes = UNSETTABLE.contains(&name) || shortcut.is_some_and(|a| HOTKEYS.contains(&a));
            return match goes {
                true => Ok(Change::Unset(name.into())),
                false => Err(bad(format!("{name}: cannot be taken out"))),
            };
        }
        let Some(cleaned) = rule(name, name, value, config) else {
            return Err(bad(format!("unknown setting {}", repr(name))));
        };
        let cleaned = toml_of(&cleaned?).ok_or_else(unwritable)?;
        Ok(Change::Value(name.into(), cleaned))
    }

    /// `services.<name>.<option>`, as the service's options say. `null` takes it out: the
    /// service's default holds again.
    fn check_option(&self, name: &str, value: &Value, config: &Config) -> Answer<Change> {
        let parts: Vec<&str> = name.split('.').collect();
        if parts.len() != 3 || parts.iter().any(|part| part.is_empty()) {
            return Err(bad(format!("unknown setting {}", repr(name))));
        }
        let (service, wanted) = (parts[1], parts[2]);
        let found = crate::services::parts();
        let declared: Vec<(String, String, Value)> = match found.get(service) {
            Some(part) => (part.options)()
                .into_iter()
                .map(|(key, kind, default)| (key.to_string(), kind.to_string(), default))
                .collect(),
            // (a plugin's service: the options it declares, as the plugin host tells them)
            None => {
                let plugins = crate::plugins::catalog(&self.home.settings).unwrap_or_default();
                let theirs = crate::plugins::entries(&plugins, "services");
                let Some(entry) = theirs
                    .iter()
                    .find(|s| s.get("name") == Some(&text(service)))
                else {
                    return Err(refusal(
                        format!("{service} is not an installed service"),
                        404,
                    ));
                };
                crate::plugins::entries(entry, "options")
                    .iter()
                    .map(|option| {
                        let got = |key: &str| option.get(key).cloned().unwrap_or(Value::None);
                        let as_text = |key: &str| got(key).as_str().unwrap_or("").to_string();
                        (as_text("key"), as_text("type"), got("default"))
                    })
                    .collect()
            }
        };
        let Some((_, kind, default)) = declared.iter().find(|(key, _, _)| key == wanted) else {
            return Err(bad(format!("{service} has no option {}", repr(wanted))));
        };
        let kind = kind.as_str();
        if kind == "string" {
            // (a file or a folder is a text)
            self.check_rights(name, value, config, &plain(default))?;
        }
        if *value == Value::None {
            return Ok(Change::Unset(name.into()));
        }
        let cleaned = option(name, value, kind)?;
        let cleaned = toml_of(&cleaned).ok_or_else(unwritable)?;
        Ok(Change::Value(name.into(), cleaned))
    }

    // -- the panel ------------------------------------------------------------------------

    /// The panel that is found (looked for on the bus, without opening it, so a running
    /// panel is not disturbed) and what the video mode makes of it.
    pub fn panel(&self) -> Answer<Value> {
        let config = self.config()?;
        let configured = lp_devices::models::find_model(&config.device.model);
        let mut found = find_panel(&config);
        if let (Some(configured), Some(on_bus)) = (configured, &found) {
            if screen_of(configured, on_bus.model) {
                found = Some(Found::of(configured));
            }
        }
        let model = configured.or(found.as_ref().map(|found| found.model));
        let video = &config.device.video;
        let usb = model.is_none_or(|model| model.protocol == "usb-turing");
        let gaps = gaps_of(&video.mode, &video.local_clip, model, usb);
        let on = usb && video_on(&video.mode, &video.local_clip, model);
        let (mut running, mut shown, mut spare) = (Value::None, None, Vec::new());
        if let Some(app) = &self.controls {
            let state = app.panel.state();
            let of = |key: &str| state.get(key).cloned().unwrap_or(Value::None);
            if of("state").as_str() == Some("showing") {
                spare = spare_panels();
            }
            shown = of("theme")
                .as_str()
                .filter(|t| !t.is_empty())
                .map(str::to_string);
            running = dict([
                ("state", of("state")),
                ("detail", of("detail")),
                ("size", of("size")),
                // the panels beside it as they are doing (Diagnose's rows for panel 2, 3 …)
                (
                    "more",
                    state
                        .get("more")
                        .cloned()
                        .unwrap_or(Value::List(Vec::new())),
                ),
            ]);
        }
        let maybe = match found.is_none() && configured.is_none() {
            true => maybe_panels(),
            false => Vec::new(),
        };
        let unseen = range_unseen(on, &gaps, &config.device.video_range, model);
        let weather_theme = self.weather_theme(&config, shown.as_deref());
        Ok(dict([
            ("configured", text(&config.device.model)),
            ("found", found.map_or(Value::None, |found| found.info)),
            // each panel beside this one: whether smooth motion runs on it, what it lacks
            ("more_video", Value::List(more_video(&config))),
            // a port with the ids other USB devices have too: the person says what it is
            ("maybe", Value::List(maybe)),
            (
                "model",
                model.map_or(Value::None, |m| model_info(m, None, true)),
            ),
            ("running", running),
            // panels plugged in that no panel of the app shows anything on: one more to set up
            ("spare", Value::List(spare)),
            (
                "video",
                dict([
                    ("mode", text(&video.mode)),
                    ("fps", Value::int(video.fps)),
                    ("on", Value::Bool(on)),
                    ("usb", Value::Bool(usb)),
                    // a display that takes whole pictures at the video's frames a second,
                    // without the video mode (udisp)
                    (
                        "streams",
                        Value::Bool(model.is_some_and(|m| m.protocol == "usb-udisp")),
                    ),
                    ("gaps", texts(&gaps)),
                    ("range_unseen", Value::Bool(unseen)),
                ]),
            ),
            (
                "weather_theme",
                weather_theme.map_or(Value::None, Value::Str),
            ),
        ]))
    }

    /// The theme on the panel (else the normal one) when it shows the weather, which the app
    /// then asks for while the weather is off: its id, else None.
    fn weather_theme(&self, config: &Config, shown: Option<&str>) -> Option<String> {
        let places = self.home.places();
        let normal = match config.theme_given {
            true => config.theme.clone(),
            false => lp_theme::defaults::default_theme(panel_model(config), &places).to_string(),
        };
        let mut themes: Vec<String> = shown.map(str::to_string).into_iter().collect();
        if !themes.contains(&normal) && !normal.is_empty() {
            themes.push(normal);
        }
        themes.into_iter().find(|theme| {
            let reads = self.theme_reads(theme);
            reads.iter().any(|key| key.starts_with("weather."))
        })
    }

    /// theme_reads: the sensor keys (and patterns) the theme reads, its modules taken apart
    /// as the panel draws them; none for a theme that cannot be read.
    fn theme_reads(&self, theme: &str) -> std::collections::HashSet<String> {
        let env = lp_theme::Env {
            fonts_dir: self.home.fonts(),
        };
        let options = lp_render::Options {
            preview: false,
            animate: false,
            dither: false,
        };
        lp_theme::versions::find_theme(theme, &self.home.places())
            .ok()
            .and_then(|folder| lp_theme::load_theme(&folder, &env).ok())
            .and_then(|theme| lp_render::Renderer::new(theme, &env, options).ok())
            .map(|renderer| renderer.reads())
            .unwrap_or_default()
    }

    /// Whether Start is to ask for the name of the panel's standby clip, and the name the
    /// vendor app keeps it under, which the question offers. Nothing is written by asking.
    pub fn clip_question(&self) -> Answer<Value> {
        let device = self.config()?.device;
        let video = &device.video;
        let waits = ["auto", "turzx"].contains(&device.driver.as_str())
            && video.mode == "auto"
            && video.local_clip.is_empty()
            && !video.clip_asked;
        // (the bus is looked at last)
        let ask = waits && {
            let model = lp_devices::models::find_model(&device.model)
                .or_else(|| lp_devices::usb::present_model(&device.beside, 0));
            model.is_some_and(|model| {
                lp_devices::turzx::video_gaps(&video.local_clip, Some(model), true) == ["clip"]
            })
        };
        Ok(dict([
            ("ask", Value::Bool(ask)),
            ("name", text("usr/data/standby.h264")),
        ]))
    }

    // -- the system -----------------------------------------------------------------------

    fn autostart(&self) -> (crate::autostart::Autostart, Vec<String>) {
        let command = crate::autostart::launch_command(Some(self.config_file));
        (crate::autostart::Autostart::new(&command[0]), command)
    }

    /// unlock: what the setup of this system would bring and how a run of it is going.
    /// Windows: the sensor service and what else "Unlock all sensors" sets up. Linux: the
    /// panel's permission, the mainboard's sensor driver and the processor's power. Both have
    /// `platform`, `available`, `command` and `job`.
    pub fn unlock(&self) -> Answer<Value> {
        if cfg!(target_os = "linux") {
            let facts = crate::linux_setup::gather(&crate::linux_setup::root());
            return Ok(Value::Dict(crate::linux_setup::status(&facts, true)));
        }
        let mut state = crate::system_setup::unlock_state();
        if cfg!(windows) {
            state.insert("presentmon".into(), self.presentmon());
        }
        state.insert(
            "command".into(),
            Value::Str(cli_command(
                "system-setup --service --pawnio --perf-log-users",
            )),
        );
        Ok(Value::Dict(state))
    }

    /// Whether the program that reads the frame rates of games is there; None while
    /// config.toml cannot be read.
    fn presentmon(&self) -> Value {
        let Ok(config) = self.config() else {
            return Value::None;
        };
        let path = config
            .sensors
            .options
            .get("presentmon")
            .and_then(|options| options.get("path"))
            .and_then(|path| match path {
                lp_py::toml::Toml::Str(path) => Some(path.clone()),
                _ => None,
            })
            .unwrap_or_default();
        Value::Bool(lp_sensors::presentmon::find_presentmon(&path, &mut Vec::new()).is_some())
    }

    /// set_unlock: `{"action": "unlock"}` (or `"remove"`): `libre-panel system-setup` with the
    /// system's own question for administrator rights (UAC on Windows, polkit's password window
    /// on Linux, where `"rapl"` also opens or closes the energy counters). The answer comes at
    /// once; the run goes on in the background.
    pub fn set_unlock(&self, payload: &Value) -> Answer<Value> {
        let payload = payload.as_dict();
        if cfg!(target_os = "linux") {
            return self.unlock_linux(payload);
        }
        let action = payload
            .and_then(|p| p.get("action"))
            .and_then(Value::as_str);
        let Some(action) = action.filter(|a| ["unlock", "remove"].contains(a)) else {
            return Err(refusal(
                "expected {\"action\": \"unlock\"} or {\"action\": \"remove\"}",
                400,
            ));
        };
        let program = crate::system_setup::cli_program();
        let available = self
            .unlock()?
            .as_dict()
            .and_then(|state| state.get("available"))
            .is_some_and(Value::truthy);
        let (true, Some(program)) = (available, program) else {
            return Err(refusal(
                lp_i18n::t("The sensor service comes with the Windows downloads (setup or zip)."),
                409,
            ));
        };
        let user = crate::system_setup::current_user();
        crate::system_setup::start(action, &program, user.as_deref());
        self.unlock()
    }

    /// The Linux side of set_unlock: pkexec shows the password dialog, and what the run is to
    /// do is only ever the two switches of `system-setup --linux`.
    fn unlock_linux(&self, payload: Option<&lp_py::Dict>) -> Answer<Value> {
        use crate::linux_setup as setup;
        let valid = payload.is_some_and(|p| p.keys().all(|k| k == "action" || k == "rapl"));
        let action = payload
            .filter(|_| valid)
            .and_then(|p| p.get("action"))
            .and_then(Value::as_str)
            .filter(|a| ["unlock", "remove"].contains(a));
        let Some(action) = action else {
            return Err(refusal(
                "expected {\"action\": \"unlock\"} or {\"action\": \"remove\"}",
                400,
            ));
        };
        let rapl = match payload.and_then(|p| p.get("rapl")) {
            None | Some(Value::None) => None,
            Some(Value::Bool(flag)) => Some(*flag),
            Some(_) => return Err(refusal("rapl: expected true or false", 400)),
        };
        if rapl.is_some() && action == "remove" {
            return Err(refusal(
                "expected {\"action\": \"unlock\", \"rapl\": true}",
                400,
            ));
        }
        let options = setup::Options {
            rapl,
            remove: action == "remove",
        };
        let command = setup::program();
        let way = setup::elevation(options, command.as_deref(), false, true);
        let say = |text: &str| {
            lp_i18n::t_format(text, &[("command", Value::Str(way.terminal.clone()))])
                .unwrap_or_else(|why| why.msg)
        };
        if crate::access::elevated() {
            return Err(refusal(
                say(
                    "Libre Panel runs with administrator rights. Run this in a terminal: {command}",
                ),
                403,
            ));
        }
        if way.argv.is_empty() {
            return Err(refusal(
                say("Libre Panel cannot ask for the administrator's rights from here. Run this in a terminal: {command}"),
                409,
            ));
        }
        if !setup::start(&way, action) {
            return Err(refusal(lp_i18n::t("The setup is running already."), 409));
        }
        self.unlock()
    }

    /// Starting with the system, and the language.
    pub fn system(&self) -> Answer<Value> {
        let (autostart, command) = self.autostart();
        let config = self.config()?;
        let kind = autostart.kind();
        let windows = kind == "windows";
        let enabled = match kind {
            "unsupported" => Value::None,
            _ => autostart.is_enabled().map_or(Value::None, Value::Bool),
        };
        let program = command.first().cloned().unwrap_or_default();
        let risky = !command.is_empty()
            && crate::autostart::writable_by_user(
                Path::new(&program),
                &crate::autostart::user_home(),
            );
        let language = lp_i18n::language();
        let languages = (lp_i18n::LANGUAGES.iter())
            .map(|(code, name)| ((*code).to_string(), text(name)))
            .collect();
        Ok(dict([
            ("kind", text(kind)),
            ("enabled", enabled),
            (
                "elevated",
                Value::Bool(windows && autostart.elevated(false)),
            ),
            // a task of that name that is off or starts another program (a leftover)
            (
                "task_hint",
                Value::Str(if windows {
                    autostart.task_hint()
                } else {
                    String::new()
                }),
            ),
            ("can_elevate", Value::Bool(autostart.can_elevate())),
            (
                "location",
                Value::Str(if kind != "unsupported" {
                    autostart.location()
                } else {
                    String::new()
                }),
            ),
            (
                "command",
                Value::Str(cli_command("autostart enable --elevated")),
            ),
            ("program", Value::Str(program)),
            ("risky", Value::Bool(risky)),
            // what this run, with the highest rights, does not load or start (rights.py)
            ("held_back", held_back(&config)),
            (
                "language",
                dict([
                    ("setting", text(&config.language)),
                    ("active", text(language.code())),
                    ("languages", Value::Dict(languages)),
                ]),
            ),
        ]))
    }

    /// `{"autostart": true}` or `{"elevated": true}`: what the command line's `autostart
    /// enable` and `disable` do. Starting with the highest rights (Windows) is a thing of its
    /// own and needs an administrator; it is never done unasked.
    pub fn set_system(&self, payload: &Value) -> Answer<Value> {
        let asked = match payload {
            Value::Dict(asked)
                if !asked.is_empty()
                    && asked
                        .keys()
                        .all(|key| key == "autostart" || key == "elevated") =>
            {
                asked
            }
            _ => {
                return Err(bad(
                    "expected {\"autostart\": true} or {\"elevated\": true}",
                ))
            }
        };
        for (name, value) in asked {
            flag(name, value)?;
        }
        let (autostart, command) = self.autostart();
        let icon = self.home.data.join("assets").join("libre-panel.png");
        let failed = |why: crate::autostart::AutostartError| refusal(why.0, 409);
        let on = |key: &str| matches!(asked.get(key), Some(Value::Bool(true)));
        if asked.contains_key("elevated") {
            if on("elevated") {
                autostart.enable(&command, true, &icon).map_err(failed)?;
            } else if autostart.elevated(true) {
                // back to the plain start
                autostart.disable().map_err(failed)?;
                autostart.enable(&command, false, &icon).map_err(failed)?;
            }
        } else if on("autostart") {
            autostart.enable(&command, false, &icon).map_err(failed)?;
        } else {
            autostart.disable().map_err(failed)?;
        }
        self.system()
    }

    // -- scenes ---------------------------------------------------------------------------

    /// The scenes and the one that is on (scenes.py).
    pub fn scenes(&self) -> Answer<Value> {
        lp_config::scenes::state(self.config_file, lp_config::platform())
            .map(|state| scenes_told(&state))
            .map_err(|why| refusal(why.msg, 409))
    }

    /// `{"action": "on" | "save" | "delete", "name": ...}`: put a scene on ("": none), keep
    /// what the panels show as one, or take one out. Answers like [`Settings::scenes`].
    pub fn set_scene(&self, payload: &Value) -> Answer<Value> {
        let asked = match payload {
            Value::Dict(payload) => {
                let name = match payload.get("name") {
                    None => Some(""),
                    Some(name) => name.as_str(),
                };
                let action = payload.get("action").and_then(Value::as_str);
                action.zip(name)
            }
            _ => None,
        };
        let (path, platform) = (self.config_file, lp_config::platform());
        let done = {
            let _writing = WRITING.lock();
            match asked {
                Some(("on", name)) => lp_config::scenes::put_on(name, path, platform),
                Some(("save", name)) => lp_config::scenes::save(name, path, platform),
                Some(("delete", name)) => lp_config::scenes::delete(name, path, platform),
                _ => {
                    return Err(bad(
                        "expected {\"action\": \"on\" | \"save\" | \"delete\", \"name\": ...}",
                    ))
                }
            }
        };
        let done = done.map_err(|why| bad(why.msg))?;
        self.follow();
        Ok(scenes_told(&done))
    }

    // -- the place ------------------------------------------------------------------------

    /// `{"query": "name"}`: places of that name from Open-Meteo's search. The text typed goes
    /// there, nothing else, and only when the person asks.
    pub fn search_place(&self, payload: &Value) -> Answer<Value> {
        let query = match payload {
            Value::Dict(payload) => payload.get("query").and_then(Value::as_str),
            _ => None,
        };
        let query = query.map(lp_py::strip).filter(|query| {
            let length = query.chars().count();
            (2..=100).contains(&length)
        });
        let Some(query) = query else {
            return Err(bad("expected a name of 2 to 100 characters"));
        };
        let agent = format!("libre-panel/{}", lp_py::VERSION);
        let language = lp_i18n::language().code().to_string();
        let found = lp_sensors::weather::geocode(query, 6, &language, &agent).map_err(|why| {
            let said = lp_i18n::t_format(
                "Could not reach the place search ({error}).",
                &[("error", text(&why))],
            );
            refusal(said.unwrap_or_else(|why| why.msg), 502)
        })?;
        let mut places = Vec::new();
        for place in found {
            let number = |v: &Value| matches!(v, Value::Int(_) | Value::Float(_));
            if !number(&place.latitude) || !number(&place.longitude) {
                continue;
            }
            let parts = [&place.name, &place.region, &place.country];
            let label: Vec<&str> = parts
                .iter()
                .filter(|p| !p.is_empty())
                .map(|p| p.as_str())
                .collect();
            // a kilometre is plenty for the weather and the sun, and it is all that is kept
            let rounded = |v: &Value| match v {
                Value::Int(_) => v.clone(),
                _ => Value::Float(lp_py::round_digits(v.to_f64().unwrap_or(0.0), 2)),
            };
            places.push(dict([
                ("name", Value::Str(label.join(", "))),
                ("latitude", rounded(&place.latitude)),
                ("longitude", rounded(&place.longitude)),
            ]));
        }
        Ok(dict([("places", Value::List(places))]))
    }

    /// `{"folder": "config"}` (or `themes`, `logs`): show it in the file manager.
    pub fn open_folder(&self, payload: &Value) -> Answer<Value> {
        let name = match payload {
            Value::Dict(payload) => payload.get("folder").and_then(Value::as_str),
            _ => None,
        };
        let Some((name, folder)) = folders()
            .into_iter()
            .find(|(known, _)| Some(*known) == name)
        else {
            return Err(bad("folder is one of config, themes, logs"));
        };
        std::fs::create_dir_all(&folder)
            .and_then(|()| open_path(&folder))
            .map_err(|why| refusal(why.to_string(), 501))?;
        Ok(dict([("opened", text(name))]))
    }

    /// What a report of a panel says of this computer, for the form on GitHub: Libre
    /// Panel's version, the system, the panel found and what is plugged in that could be
    /// one, by its USB ids. The bus is only looked at: nothing is sent to a panel.
    pub fn report(&self) -> Answer<Value> {
        let config = self.config()?;
        let product = find_panel(&config).map_or(String::new(), |found| found.model.label());
        let (system, release, machine) = platform();
        let mut lines = vec![
            format!(
                "Libre Panel {}, {system} {release} ({machine})",
                lp_py::VERSION
            ),
            format!(
                "Panel found: {}",
                if product.is_empty() { "none" } else { &product }
            ),
        ];
        // (what the owner's A/B found, for a model whose range is not known yet)
        if config.device.video_range != "auto" {
            lines.push(format!("Video range: {}", config.device.video_range));
        }
        lines.extend(connected_devices());
        let report = lines.join("\n");
        let quote = lp_sensors::weather::quote_plus;
        let fields = match product.is_empty() {
            true => format!("devices={}", quote(&report)),
            false => format!("product={}&devices={}", quote(&product), quote(&report)),
        };
        Ok(dict([
            ("text", Value::Str(report)),
            ("product", Value::Str(product)),
            (
                "form",
                Value::Str(format!(
                    "{PROJECT_URL}/issues/new?template=panel_support.yml&{fields}"
                )),
            ),
            // how a terminal is opened, for the doctor
            ("platform", text(lp_config::platform())),
        ]))
    }

    // -- about ----------------------------------------------------------------------------

    pub fn about(&self) -> Answer<Value> {
        let issues = format!("{PROJECT_URL}/issues");
        let form = format!("{PROJECT_URL}/issues/new?template=panel_support.yml");
        let folders = folders().map(|(name, folder)| (name, text(&folder.display().to_string())));
        Ok(dict([
            ("version", text(lp_py::VERSION)),
            (
                "license",
                dict([
                    ("id", text("GPL-3.0-or-later")),
                    ("url", text("https://www.gnu.org/licenses/gpl-3.0.html")),
                ]),
            ),
            ("notice", Value::Str(self.notice_text())),
            (
                "links",
                dict([
                    ("project", text(PROJECT_URL)),
                    ("issues", Value::Str(issues)),
                    ("hardware_report", Value::Str(form)),
                ]),
            ),
            ("doctor", Value::Str(cli_command("doctor"))),
            ("devices", Value::Str(cli_command("devices"))),
            ("folders", dict(folders)),
        ]))
    }

    /// NOTICE.md: the copy with the app's files, else the project's own.
    fn notice_text(&self) -> String {
        let data = &self.home.data;
        let project = data.parent().and_then(Path::parent);
        let tried = [
            Some(data.join("assets").join("NOTICE.md")),
            project.map(|folder| folder.join("NOTICE.md")),
        ];
        for path in tried.into_iter().flatten() {
            if let Ok(text) = std::fs::read_to_string(path) {
                return text
                    .replace("\r\n", "\n")
                    .chars()
                    .take(MAX_NOTICE)
                    .collect();
            }
        }
        String::new()
    }
}

/// A panel on the bus, as the app shows it.
struct Found {
    model: &'static PanelModel,
    info: Value,
}

impl Found {
    fn of(model: &'static PanelModel) -> Found {
        Found {
            model,
            info: model_info(model, None, true),
        }
    }
}

const ZC360: &str = "jonsbo-zc360";
const ZC360_SCREEN: &str = "jonsbo-zc360-screen";

/// A panel model as the app shows it: its name, its size the way round it is mounted, how
/// it is connected.
fn model_info(model: &PanelModel, port: Option<&str>, sure: bool) -> Value {
    let (width, height) = model.size("landscape");
    let limits = match model.limits {
        "" => String::new(),
        limits => lp_i18n::t(limits),
    };
    dict([
        ("id", text(model.id)),
        ("label", Value::Str(model.label())),
        (
            "size",
            Value::List(vec![Value::int(width), Value::int(height)]),
        ),
        ("shape", text(model.shape)),
        (
            "connection",
            text(match model.protocol.starts_with("usb") {
                true => "usb",
                false => "serial",
            }),
        ),
        ("port", port.map_or(Value::None, text)),
        ("sure", Value::Bool(sure)),
        ("video", Value::Bool(model.video)),
        ("limits", Value::Str(limits)),
        // screens that show it together (the ZC-360: three)
        ("screens", Value::int(if model.id == ZC360 { 3 } else { 1 })),
    ])
}

fn port_info(port: &lp_devices::ports::FoundPort) -> Value {
    model_info(port.model, Some(&port.device), port.sure())
}

/// Whether the panel set up is one screen of the panel on the bus: a screen of the ZC-360
/// as a panel of its own, which the bus shows as the three together.
fn screen_of(configured: &PanelModel, found: &PanelModel) -> bool {
    configured.id == ZC360_SCREEN && found.id == ZC360
}

/// _find_panel: a panel on the bus, none of it opened: a TURZX USB one, another maker's USB
/// screen, a ZC-360, else a serial one by its USB ids.
fn find_panel(config: &Config) -> Option<Found> {
    // a TURZX USB one, another maker's USB screen, a ZC-360, else a serial one by its USB ids
    let model = lp_devices::usb::present_model(&config.device.beside, 0)
        .or_else(crate::display::other_usb_model)
        .or_else(|| {
            lp_devices::jonsbo_zc360::present()
                .then(|| lp_devices::models::find_model(lp_devices::jonsbo_zc360::MODEL_ID))
                .flatten()
        });
    if let Some(model) = model {
        return Some(Found::of(model));
    }
    // (a port that is not a panel's is no panel)
    let port = lp_devices::ports::find_port(None, &config.device.port).ok()??;
    Some(Found {
        model: port.model,
        info: port_info(&port),
    })
}

/// panel_model: the panel a config is for, as far as it is known without opening anything:
/// the configured model; else, with "auto" and a driver that looks for TURZX USB panels,
/// the one on the USB bus.
pub fn panel_model(config: &Config) -> Option<&'static PanelModel> {
    let device = &config.device;
    lp_devices::models::find_model(&device.model).or_else(|| {
        let looks = device.model == "auto" && ["auto", "turzx"].contains(&device.driver.as_str());
        looks
            .then(|| lp_devices::usb::present_model(&device.beside, 0))
            .flatten()
    })
}

/// What the video mode lacks on a panel, as codes; none where it is off or the panel is no
/// TURZX USB one.
fn gaps_of(mode: &str, clip: &str, model: Option<&PanelModel>, usb: bool) -> Vec<&'static str> {
    match mode {
        _ if !usb => Vec::new(),
        "off" => Vec::new(),
        "auto" => match model {
            Some(model) => lp_devices::turzx::video_gaps(clip, Some(model), true),
            None => Vec::new(),
        },
        _ => lp_devices::turzx::video_gaps(clip, model, false),
    }
}

/// video_mode's first: whether a TURZX USB panel runs in video mode.
fn video_on(mode: &str, clip: &str, model: Option<&PanelModel>) -> bool {
    match mode {
        "auto" => {
            model.is_some_and(|m| lp_devices::turzx::video_gaps(clip, Some(m), true).is_empty())
        }
        mode => mode == "on",
    }
}

/// Whether Start asks how black looks on a panel: smooth motion runs on it (nothing it
/// lacks), its range is left to the model, and nobody has seen that model's range on a
/// panel.
fn range_unseen(on: bool, gaps: &[&str], range: &str, model: Option<&PanelModel>) -> bool {
    on && gaps.is_empty() && range == "auto" && !model.is_some_and(|m| m.video_range_seen)
}

/// Smooth motion on each panel of `[[panels]]`: `{index, usb, on, gaps}`, as for the first
/// panel (its own mode and clip), the model as set.
fn more_video(config: &Config) -> Vec<Value> {
    (0..config.panels.len())
        .filter_map(|n| {
            let device = config.for_panel(n)?.device;
            let video = &device.video;
            let model = lp_devices::models::find_model(&config.panels[n].device.model);
            let usb = model.is_none_or(|model| model.protocol == "usb-turing");
            let gaps = gaps_of(&video.mode, &video.local_clip, model, usb);
            let on = usb && video_on(&video.mode, &video.local_clip, model);
            let unseen = range_unseen(on, &gaps, &device.video_range, model);
            Some(dict([
                ("index", Value::int(n as i64)),
                ("usb", Value::Bool(usb)),
                ("on", Value::Bool(on)),
                ("gaps", texts(&gaps)),
                ("range_unseen", Value::Bool(unseen)),
            ]))
        })
        .collect()
}

/// _spare_panels: the panels on the bus that no loop of the app has open: each with its
/// model and, for a serial one, its port.
fn spare_panels() -> Vec<Value> {
    let usb = lp_devices::usb::free_models();
    let mut found: Vec<Value> = usb.into_iter().map(|m| model_info(m, None, true)).collect();
    found.extend(lp_devices::ports::free_panels().iter().map(port_info));
    found
}

/// _maybe_panels: the ports with the generic ids of an awake Turing rev. C panel, which
/// Libre Panel does not talk to unasked: each with the panels it may be.
fn maybe_panels() -> Vec<Value> {
    lp_devices::ports::gadget_ports()
        .iter()
        .map(|port| {
            let models = port.candidates.iter();
            let models = models.map(|m| model_info(m, Some(&port.device), false));
            dict([
                ("port", text(&port.device)),
                ("models", Value::List(models.collect())),
            ])
        })
        .collect()
}

/// `{plugins, programs}`: the plugin folders not loaded and the settings whose program is
/// not started because Libre Panel runs with the highest rights; empty without them.
fn held_back(config: &Config) -> Value {
    let mut programs = Vec::new();
    if lp_devices::rights::elevated() {
        let option = |source: &str, key: &str| {
            let table = config.sensors.options.get(source);
            match table.and_then(|table| table.get(key)) {
                Some(Toml::Str(text)) => text.clone(),
                Some(other) => lp_py::format::str_of(&value_of(other)),
                None => String::new(),
            }
        };
        let named = [
            ("video.ffmpeg", config.device.video.ffmpeg.clone()),
            ("[sensors.presentmon] path", option("presentmon", "path")),
        ];
        for (setting, path) in named {
            let file = expanduser(&path);
            if !path.is_empty() && file.is_file() && !lp_devices::rights::safe_place(&file) {
                programs.push(setting);
            }
        }
        let libraries = config.sensors.options.get("librehardwaremonitor");
        if let Some(Toml::Str(folder)) = libraries.and_then(|table| table.get("dll_dir")) {
            if !folder.is_empty() && !lp_devices::rights::safe_place(Path::new(folder)) {
                programs.push("[sensors.librehardwaremonitor] dll_dir"); // (its libraries)
            }
        }
    }
    dict([
        ("plugins", Value::List(Vec::new())),
        ("programs", texts(&programs)),
    ])
}

/// Path.expanduser: `~` at the start as the user's folder.
fn expanduser(path: &str) -> std::path::PathBuf {
    match path.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with(['/', '\\']) => {
            let rest = rest.trim_start_matches(['/', '\\']);
            crate::autostart::user_home().join(rest)
        }
        _ => std::path::PathBuf::from(path),
    }
}

/// open_path: show a file or folder with the system's default program (Windows, or
/// xdg-open).
pub fn open_path(path: &Path) -> std::io::Result<()> {
    if cfg!(windows) {
        std::process::Command::new("explorer")
            .arg(path)
            .spawn()
            .map(drop)
    } else if lp_devices::rights::which("xdg-open", &mut Vec::new()).is_some() {
        std::process::Command::new("xdg-open")
            .arg(path)
            .spawn()
            .map(drop)
    } else {
        Err(std::io::Error::other(lp_i18n::t(
            "Opening a file or folder is not supported on this system: no xdg-open.",
        )))
    }
}

/// platform.system(), .release() and .machine().
pub fn platform() -> (String, String, String) {
    #[cfg(unix)]
    {
        // SAFETY: uname fills the struct it is given.
        let mut name: libc::utsname = unsafe { std::mem::zeroed() };
        if unsafe { libc::uname(&mut name) } == 0 {
            let field = |chars: &[libc::c_char]| {
                let bytes: Vec<u8> = chars
                    .iter()
                    .take_while(|c| **c != 0)
                    .map(|c| *c as u8)
                    .collect();
                String::from_utf8_lossy(&bytes).into_owned()
            };
            return (
                field(&name.sysname),
                field(&name.release),
                field(&name.machine),
            );
        }
        (String::new(), String::new(), String::new())
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::SystemInformation::OSVERSIONINFOW;
        #[link(name = "ntdll")]
        extern "system" {
            fn RtlGetVersion(info: *mut OSVERSIONINFOW) -> i32;
        }
        // SAFETY: the struct is given its size, as RtlGetVersion asks.
        let mut info: OSVERSIONINFOW = unsafe { std::mem::zeroed() };
        info.dwOSVersionInfoSize = std::mem::size_of::<OSVERSIONINFOW>() as u32;
        unsafe { RtlGetVersion(&mut info) };
        let release = match (info.dwMajorVersion, info.dwBuildNumber) {
            (10, build) if build >= 22000 => "11".to_string(),
            (major, _) => major.to_string(),
        };
        let machine = std::env::var("PROCESSOR_ARCHITECTURE").unwrap_or_default();
        ("Windows".to_string(), release, machine)
    }
    #[cfg(not(any(unix, windows)))]
    {
        (String::new(), String::new(), String::new())
    }
}

/// connected_devices: what is plugged in that could be a panel, a line each, without serial
/// numbers: the USB serial ports with their ids and the plain USB panels, and the models each
/// could be. Only the system's lists are read; no port is opened.
fn connected_devices() -> Vec<String> {
    let models = |vid: u16, pid: u16| {
        let found = lp_devices::models::models_for_usb(vid, pid, None);
        let ids: Vec<&str> = found.iter().map(|m| m.id).collect();
        match ids.is_empty() {
            true => String::new(),
            false => format!("  -> {}", ids.join(" / ")),
        }
    };
    let mut lines = Vec::new();
    for port in lp_devices::serial::ports() {
        // a port without USB ids (a mainboard's, Bluetooth) is no panel
        let (Some(vid), Some(pid)) = (port.vid, port.pid) else {
            continue;
        };
        let about = lp_devices::serial::product_of(&port.device).unwrap_or("n/a".into());
        lines.push(format!(
            "{}  {vid:04x}:{pid:04x}  {about}{}",
            port.device,
            models(vid, pid)
        ));
    }
    // the plain USB panels by their vendors, and the catalog's other USB screens by their ids
    const VENDORS: [u16; 3] = [0x1CBE, 0x43A8, 0x345F];
    let all = lp_devices::usb::all_ids();
    let mut products: Vec<(u16, u16)> = (lp_devices::models::MODELS.iter())
        .filter(|m| m.protocol.starts_with("usb"))
        .flat_map(|m| m.usb_ids.iter().copied())
        .filter(|(vid, _)| !VENDORS.contains(vid))
        .collect();
    products.sort_unstable();
    products.dedup();
    for vendor in VENDORS {
        for (vid, pid) in all.iter().filter(|(vid, _)| *vid == vendor) {
            lines.push(format!("usb  {vid:04x}:{pid:04x}{}", models(*vid, *pid)));
        }
    }
    for wanted in products {
        for (vid, pid) in all.iter().filter(|ids| **ids == wanted) {
            lines.push(format!("usb  {vid:04x}:{pid:04x}{}", models(*vid, *pid)));
        }
    }
    match lines.is_empty() {
        true => vec!["Nothing plugged in looks like a panel.".to_string()],
        false => lines,
    }
}

fn scenes_told(state: &lp_config::scenes::State) -> Value {
    let scenes = state
        .scenes
        .iter()
        .map(|scene| value_of(&Toml::Table(scene.clone())))
        .collect();
    dict([("scenes", Value::List(scenes)), ("on", text(&state.on))])
}

/// What only holds across settings: the place is both coordinates or none, and the weather
/// needs one.
fn check_together(changes: &Dict, config: &Config) -> Answer<()> {
    let weather = &config.weather;
    let after = |name: &str, current: Value| changes.get(name).cloned().unwrap_or(current);
    let lat = after(
        "weather.latitude",
        weather.latitude.map_or(Value::None, Value::Float),
    );
    let lon = after(
        "weather.longitude",
        weather.longitude.map_or(Value::None, Value::Float),
    );
    let (no_lat, no_lon) = (matches!(lat, Value::None), matches!(lon, Value::None));
    let placed =
        changes.contains_key("weather.latitude") || changes.contains_key("weather.longitude");
    if placed && no_lat != no_lon {
        return Err(bad("weather.latitude and weather.longitude go together"));
    }
    let enabled = after("weather.enabled", Value::Bool(weather.enabled));
    if matches!(enabled, Value::Bool(true)) && (no_lat || no_lon) {
        return Err(bad(lp_i18n::t(
            "Choose a place first: the weather needs it.",
        )));
    }
    Ok(())
}

/// A panel's `video`: `{mode, local_clip, clip_asked}`, what differs from it to begin with
/// kept (`{}` for none).
fn panel_video(name: &str, value: &Value) -> Answer<Value> {
    const KEYS: [&str; 3] = ["mode", "local_clip", "clip_asked"];
    let given = match value {
        Value::Dict(given) if given.keys().all(|key| KEYS.contains(&key.as_str())) => given,
        _ => {
            return Err(bad(format!(
                "{name}: expected {{mode, local_clip, clip_asked}}"
            )))
        }
    };
    let mode = given.get("mode").cloned().unwrap_or(text("off"));
    if !matches!(mode.as_str(), Some("auto" | "off" | "on")) {
        return Err(bad(format!("{name}.mode: \"auto\", \"off\" or \"on\"")));
    }
    let clip = given.get("local_clip").cloned().unwrap_or(text(""));
    let clip = a_text(&format!("{name}.local_clip"), &clip, 200)?;
    let asked = given
        .get("clip_asked")
        .cloned()
        .unwrap_or(Value::Bool(false));
    let asked = flag(&format!("{name}.clip_asked"), &asked)?;
    let begins = [
        ("mode", text("off")),
        ("local_clip", text("")),
        ("clip_asked", Value::Bool(false)),
    ];
    let kept = [mode, clip, asked]
        .into_iter()
        .zip(begins)
        .filter(|(item, (_, begin))| item != begin)
        .map(|(item, (key, _))| (key.to_string(), item))
        .collect();
    Ok(Value::Dict(kept))
}

/// `panels`: the panels beside this one, each a table of `theme`, `on` and the keys of
/// `[device]`, as read() hands them; written as `[[panels]]` with what differs from the
/// defaults.
fn check_panels(value: &Value, config: &Config) -> Answer<Vec<Table>> {
    let listed = match value {
        Value::List(listed) if listed.len() <= lp_config::MORE_PANELS => listed,
        _ => {
            return Err(bad(format!(
                "panels: a list of at most {} panels",
                lp_config::MORE_PANELS
            )))
        }
    };
    let mut tables = Vec::new();
    for (n, table) in listed.iter().enumerate() {
        let Value::Dict(table) = table else {
            return Err(bad(format!("panels[{n}]: expected a table")));
        };
        let Value::Dict(mut default) = dict([
            ("theme", text("")),
            ("on", Value::Bool(true)),
            ("follow_brightness", Value::Bool(true)),
            ("brightness_offset", Value::int(0)),
            ("game_theme", text("")),
            ("media_theme", text("")),
            ("alarm_theme", text(lp_config::ALARM_THEME)),
            ("same_look", Value::Bool(false)),
            ("follow", Value::None),
            ("video", Value::Dict(Dict::default())),
        ]) else {
            unreachable!()
        };
        let device = panel_device(n);
        default.extend(device_table(&device));
        let now = config
            .panels
            .get(n)
            .map_or(&device.output, |panel| &panel.device.output);
        let mut kept = Dict::default();
        for (key, item) in table {
            let name = format!("panels[{n}].{key}");
            let cleaned = match key.as_str() {
                "theme" | "game_theme" | "media_theme" | "alarm_theme" => a_text(&name, item, 64)?,
                "on" | "follow_brightness" | "same_look" => flag(&name, item)?,
                // (None: as its theme says)
                "follow" if *item == Value::None => Value::None,
                "follow" => flag(&name, item)?,
                "brightness_offset" => whole(&name, item, -50, 50)?,
                "video" => panel_video(&name, item)?,
                _ => match default.contains_key(key) {
                    true => rule(&format!("device.{key}"), &name, item, config)
                        .unwrap_or_else(|| Err(bad(format!("unknown setting {}", repr(&name)))))?,
                    false => return Err(bad(format!("unknown setting {}", repr(&name)))),
                },
            };
            let other = cleaned.as_str().is_none_or(|output| output != now);
            if key == "output" && other && crate::access::elevated() {
                return Err(refused_with_rights(&name)); // (as device.output)
            }
            if Some(&cleaned) != default.get(key) {
                kept.insert(key.clone(), cleaned);
            }
        }
        let wrong = || bad(format!("panels[{n}]: expected a table"));
        tables.push(table_of(&kept).ok_or_else(wrong)?);
    }
    Ok(tables)
}

fn pairs(name: &str, value: &Value) -> Answer<Table> {
    let given = match value {
        Value::Dict(given) if given.len() <= 100 => given,
        _ => return Err(bad(format!("{name}: a table of at most 100 names"))),
    };
    let mut found = Table::new();
    for (key, item) in given {
        let key = plain_text(&format!("{name}.{key}"), &text(key), 40, true)?;
        let item = plain_text(&format!("{name}.{key}"), item, 200, false)?;
        found.insert(key, Toml::Str(item));
    }
    Ok(found)
}

/// `sensors.json.sources` as the app's form sends it (each `{name, where, fields, units}`):
/// written as `[[sensors.json.sources]]`, `where` as `url` (http:// or https://) or `file`,
/// the headers of a source of the same name kept. A file not named yet is refused while
/// Libre Panel runs with administrator rights.
fn check_json_sources(value: &Value, config: &Config) -> Answer<Vec<Table>> {
    let listed = match value {
        Value::List(listed) if listed.len() <= JSON_MOST => listed,
        _ => {
            return Err(bad(format!(
                "{JSON_SOURCES}: a list of at most {JSON_MOST} sources"
            )))
        }
    };
    let json = config.sensors.options.get("json");
    let entries: Vec<&Table> = match json.and_then(|json| json.get("sources")) {
        Some(Toml::Array(entries)) => entries
            .iter()
            .filter_map(|entry| match entry {
                Toml::Table(entry) => Some(entry),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    };
    let said = |entry: &Table, key: &str| {
        lp_py::format::str_of(&entry.get(key).map_or(Value::None, value_of))
    };
    let truthy = |entry: &Table, key: &str| entry.get(key).is_some_and(|v| value_of(v).truthy());
    let before: Vec<(String, &Toml)> = entries
        .iter()
        .filter(|entry| truthy(entry, "headers"))
        .map(|entry| (said(entry, "name"), &entry["headers"]))
        .collect();
    // read with the rights Libre Panel has: a new one only in config.toml then
    let files: Vec<String> = entries
        .iter()
        .filter(|entry| truthy(entry, "file"))
        .map(|entry| said(entry, "file"))
        .collect();
    let (mut tables, mut names) = (Vec::new(), Vec::<String>::new());
    for (n, source) in listed.iter().enumerate() {
        let at = format!("{JSON_SOURCES}[{n}]");
        let Value::Dict(source) = source else {
            return Err(bad(format!("{at}: expected a table")));
        };
        let of = |key: &str| source.get(key).cloned().unwrap_or(Value::None);
        let name = plain_text(&format!("{at}.name"), &of("name"), 40, true)?;
        let fits = |ch: char| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-';
        if !name.chars().all(fits) {
            return Err(bad(format!("{at}.name: letters, digits, - and _ only")));
        }
        if names.contains(&name) {
            return Err(bad(format!("{at}.name: {} is there twice", repr(&name))));
        }
        names.push(name.clone());
        let place = plain_text(&format!("{at}.where"), &of("where"), 300, true)?;
        let mut table = Table::new();
        table.insert("name".into(), Toml::Str(name.clone()));
        let url = place.starts_with("http://") || place.starts_with("https://");
        if !url && !files.contains(&place) && crate::access::elevated() {
            return Err(refused_with_rights(&format!("{at}.where")));
        }
        table.insert(if url { "url" } else { "file" }.into(), Toml::Str(place));
        let or_none = |value: Value| match value.truthy() {
            true => value,
            false => Value::Dict(Dict::default()),
        };
        let fields = pairs(&format!("{at}.fields"), &or_none(of("fields")))?;
        let units = pairs(&format!("{at}.units"), &or_none(of("units")))?;
        if !fields.is_empty() {
            table.insert("fields".into(), Toml::Table(fields));
        }
        if !units.is_empty() {
            table.insert("units".into(), Toml::Table(units));
        }
        // (the last of a name counts, as in a dict)
        if let Some((_, headers)) = before.iter().rev().find(|(known, _)| *known == name) {
            table.insert("headers".into(), (*headers).clone());
        }
        tables.push(table);
    }
    Ok(tables)
}

/// `switches.<switch>`: on or off for every theme that has it; `switches.<theme>.<switch>`
/// for that theme and all its versions. `null` takes the choice out.
fn check_switch(name: &str, value: &Value) -> Answer<Change> {
    let fits = |part: &&str| {
        // (as the pattern's `$`: a line's end may follow)
        let part = part.strip_suffix('\n').unwrap_or(part);
        let small = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
        let rest = |b: u8| small(b) || b == b'_' || b == b'-';
        let bytes = part.as_bytes();
        (1..=64).contains(&bytes.len()) && small(bytes[0]) && bytes[1..].iter().all(|b| rest(*b))
    };
    let parts: Vec<&str> = name.split('.').collect();
    if !(parts.len() == 2 || parts.len() == 3) || !parts[1..].iter().all(fits) {
        return Err(bad(format!("unknown setting {}", repr(name))));
    }
    if *value == Value::None {
        return Ok(Change::Unset(name.into()));
    }
    let Value::Bool(on) = flag(name, value)? else {
        unreachable!()
    };
    Ok(Change::Value(name.into(), Toml::Bool(on)))
}

fn wrong_kind(name: &str, value: &Value, kind: &str) -> Refusal {
    let got = match value {
        Value::Bool(_) => "a boolean",
        other => other.type_name(),
    };
    bad(format!("{name}: expected <class '{kind}'>, got {got}"))
}

/// The cards of the card slots (config.card_choice), in the order they are asked.
fn rule_cards(name: &str, value: &Value) -> Answer<Value> {
    let Value::List(lines) = value else {
        return Err(wrong_kind(name, value, "list"));
    };
    if let Some(other) = lines.iter().find(|line| line.as_str().is_none()) {
        return Err(wrong_kind(&format!("{name}[]"), other, "str"));
    }
    let lines = toml_of(value).unwrap_or(Toml::Array(Vec::new()));
    match lp_config::card_choice(&lines, name) {
        Ok(cards) => Ok(texts(&cards)),
        Err(why) => Err(bad(why.msg)),
    }
}

/// Where a panel stands beside the others (`at`): [x, y] in millimetres, or None.
fn rule_at(name: &str, value: &Value) -> Answer<Value> {
    let place = match value {
        Value::None => None,
        Value::List(items) => Some(Toml::Array(
            (items.iter())
                .map(|item| match item {
                    Value::Int(n) => Toml::Int(n.clone()),
                    _ => Toml::Bool(false), // (no whole number: refused)
                })
                .collect(),
        )),
        _ => Some(Toml::Bool(false)),
    };
    match lp_config::place_at(place.as_ref(), name) {
        Ok(Some((x, y))) => Ok(Value::List(vec![Value::int(x), Value::int(y)])),
        Ok(None) => Ok(Value::None),
        Err(why) => Err(bad(why.msg)),
    }
}

/// Empty (the themes' own colours), "system" (Windows') or a colour such as "#E0457B".
fn rule_accent(name: &str, value: &Value) -> Answer<Value> {
    let given = plain_text(name, value, 7, false)?;
    let colour = given.strip_prefix('#');
    let hex = colour.is_some_and(|c| c.len() == 6 && c.bytes().all(|b| b.is_ascii_hexdigit()));
    if !given.is_empty() && given != lp_config::ACCENT_SYSTEM && !hex {
        return Err(bad(format!(
            "{name}: \"system\", a colour such as \"#E0457B\", or empty"
        )));
    }
    Ok(Value::Str(match colour {
        Some(_) => given.to_uppercase(),
        None => given,
    }))
}

fn rule_providers(name: &str, value: &Value, config: &Config) -> Answer<Value> {
    let given: Option<Vec<&str>> = match value {
        Value::List(items) => items.iter().map(Value::as_str).collect(),
        _ => None,
    };
    let Some(given) = given else {
        return Err(bad(format!("{name}: expected a list of source names")));
    };
    let known = |item: &str| {
        PROVIDERS.contains(&item) || config.sensors.providers.iter().any(|p| p == item)
    };
    if let Some(unknown) = given.iter().find(|item| !known(item)) {
        return Err(bad(format!("{name}: unknown source {}", repr(unknown))));
    }
    let mut chosen: Vec<&str> = Vec::new();
    for item in given {
        if !chosen.contains(&item) {
            chosen.push(item);
        }
    }
    if chosen.is_empty() {
        return Err(bad(format!("{name}: at least one source")));
    }
    // what the files of 0.4 named alone: config.py reads it as the usual list
    if chosen == ["psutil"] {
        return Err(bad(format!(
            "{name}: psutil alone stands for the usual list; keep another source"
        )));
    }
    Ok(texts(&chosen))
}

/// A shortcut, written the one way (`Ctrl+Alt+P`), that no other action has.
fn rule_hotkey(name: &str, value: &Value, config: &Config) -> Answer<Value> {
    // (the text's own refusal is a ValueError too: it is named twice, as Python names it)
    let given = plain_text(name, value, 40, true)
        .map_err(|refused| bad(format!("{name}: {}", refused.message)))?;
    let combo = match lp_config::hotkeys::parse(&given) {
        Ok(combo) => combo.to_string(),
        Err(why) => return Err(bad(format!("{name}: {why}"))),
    };
    let action = name.split_once('.').map_or("", |(_, action)| action);
    let twin = config.hotkeys.iter().find(|(_, held)| **held == combo);
    if twin.is_some_and(|(other, _)| other != action) {
        let said = lp_i18n::t_format(
            "{combo} is the shortcut of another action already",
            &[("combo", text(&combo))],
        );
        return Err(bad(said.unwrap_or_else(|why| why.msg)));
    }
    Ok(Value::Str(combo))
}

/// A transition by its name: one Libre Panel brings, or "random"; "" where the occasion may
/// leave it to the service or the default (`empty`).
fn rule_transition(name: &str, value: &Value, empty: bool) -> Answer<Value> {
    let mut allowed = known_transitions();
    allowed.push(lp_config::RANDOM_TRANSITION);
    allowed.sort_unstable();
    if empty {
        allowed.insert(0, "");
    }
    choice(name, value, &allowed)
}

/// What "random" picks from: one to 32 transitions, each once.
fn rule_random(name: &str, value: &Value) -> Answer<Value> {
    let known = known_transitions();
    let given: Option<Vec<&str>> = match value {
        Value::List(items) if (1..=32).contains(&items.len()) => {
            items.iter().map(Value::as_str).collect()
        }
        _ => None,
    };
    match given.filter(|given| given.iter().all(|item| known.contains(item))) {
        Some(given) => {
            let mut once: Vec<&str> = Vec::new();
            for item in given {
                if !once.contains(&item) {
                    once.push(item);
                }
            }
            Ok(texts(&once))
        }
        None => Err(bad(format!(
            "{name}: one to 32 transitions, by their names"
        ))),
    }
}

/// The rule of the setting `key`, applied to `value` (`name`: what an error calls it); None
/// for a setting there is no rule for.
fn rule(key: &str, name: &str, value: &Value, config: &Config) -> Option<Answer<Value>> {
    use lp_config as c;
    let speeds: Vec<&str> = c::TRANSITION_SPEEDS
        .iter()
        .map(|(speed, _)| *speed)
        .collect();
    Some(match key {
        "device.model" => match value.as_str() {
            Some(model) if model == "auto" || lp_devices::models::find_model(model).is_some() => {
                Ok(value.clone())
            }
            _ => Err(bad(format!(
                "{name}: auto, or a model from: libre-panel models"
            ))),
        },
        "device.driver" => choice(name, value, &DRIVERS),
        "device.brightness" | "device.night_brightness" | "device.away_brightness" => {
            whole(name, value, 0, 100)
        }
        "device.rotate" => choice_whole(name, value, &[0, 180]),
        "device.orientation" => choice(name, value, &["", "landscape", "portrait"]),
        "device.motion" => choice(name, value, &c::MOTIONS),
        "device.still" => choice(name, value, &c::STILLS),
        // the range of the video's values on the panel
        "device.video_range" => choice(name, value, &c::VIDEO_RANGES),
        "device.jpeg" => choice(name, value, &c::JPEG_LAYOUTS),
        "device.jpeg_quality" => whole(name, value, c::JPEG_QUALITY.0, c::JPEG_QUALITY.1),
        "device.dark_on_quit"
        | "device.dark_on_sleep"
        | "device.night"
        | "device.dark_when_away" => flag(name, value),
        "device.night_from" | "device.night_to" => time_of_day(name, value),
        "device.at" => rule_at(name, value),
        "across" => a_text(name, value, 300),
        "transitions.default" => rule_transition(name, value, false),
        "transitions.speed" => choice(name, value, &speeds),
        "transitions.random" => rule_random(name, value),
        "device.output" => plain_text(name, value, 260, true).map(Value::Str),
        "device.port" => a_text(name, value, 64),
        "video.mode" => choice(name, value, &["auto", "on", "off"]),
        "video.fps" => whole(name, value, 1, 60),
        "video.device_fps" => whole(name, value, 1, 120),
        "video.crf" => whole(name, value, 0, 51),
        "video.preset" => choice(name, value, &c::X264_PRESETS),
        "video.keyframe_s" => number(name, value, 1.0, 60.0),
        "video.maxrate" => rate(name, value),
        "video.profile" => choice(name, value, &c::PROFILES),
        "video.encoder" => choice(name, value, &c::ENCODERS),
        // with the engine: it has the panel, and it composes the frames too
        "video.engine_sends"
        | "video.engine_layers"
        | "video.direct"
        | "video.rest"
        | "video.clip_asked" => flag(name, value),
        "video.engine_encoder" => choice(name, value, &c::ENGINE_ENCODERS),
        "video.pixels" => choice(name, value, &c::PIXELS),
        "video.local_clip" => a_text(name, value, 200),
        "video.ffmpeg" => a_text(name, value, 260),
        "refresh_ms" => whole(name, value, 100, 3_600_000),
        "check_updates" | "allow_notify" | "tray_icon" | "light_colours" | "graph_steps"
        | "hotkeys.enabled" | "weather.enabled" => flag(name, value),
        "symbols" => choice(name, value, &c::SYMBOL_PACKS),
        "cards" => rule_cards(name, value),
        "card_order" => choice(name, value, lp_theme::modules::slots::CARD_ORDERS),
        "card_transition" => choice(name, value, lp_theme::modules::slots::CARD_TRANSITIONS),
        "accent" => rule_accent(name, value),
        "card_opacity" if *value == Value::int(0) => Ok(Value::int(0)),
        "card_opacity" => whole(name, value, 40, 100),
        "sensors.providers" => rule_providers(name, value, config),
        "sensors.librehardwaremonitor.mode" => choice(name, value, &LHM_MODES),
        "sensors.librehardwaremonitor.url" => plain_text(name, value, 300, true).and_then(|url| {
            match url.starts_with("http://") || url.starts_with("https://") {
                true => Ok(Value::Str(url)),
                false => Err(bad(format!(
                    "{name}: an address that starts with http:// or https://"
                ))),
            }
        }),
        "sensors.json.update_s" => whole(name, value, 2, 3600),
        "weather.units" => choice(name, value, &["metric", "imperial"]),
        "weather.update_minutes" => whole(name, value, 5, 1440),
        "weather.latitude" => number(name, value, -90.0, 90.0),
        "weather.longitude" => number(name, value, -180.0, 180.0),
        "weather.place" => a_text(name, value, 120),
        _ => {
            let (table, rest) = key.split_once('.')?;
            match table {
                "transitions" if c::TRANSITION_OCCASIONS.contains(&rest) => {
                    rule_transition(name, value, true)
                }
                "hotkeys" if HOTKEYS.contains(&rest) => rule_hotkey(name, value, config),
                _ => return None,
            }
        }
    })
}

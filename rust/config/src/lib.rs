//! config.toml as config.py reads and checks it: what a config says of the panel, the themes,
//! the sensors and the rest, with the defaults of what it leaves out; and what is wrong with
//! one, in the words Python says it.
//!
//! The same file is read by the Python app, so a setting means here what it means there: the
//! tests (tests/test_rust_config.py) hold every field and every message against config.py.

pub mod dirs;
pub mod edit;
pub mod hotkeys;
pub mod night;
pub mod scenes;
pub mod sequence;

use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use lp_py::toml::{Table, Toml};
use lp_theme::modules::slots;
use num_bigint::BigInt;
use num_traits::ToPrimitive;

pub use sequence::Step;

pub const CONFIG_FILENAME: &str = "config.toml";
pub const DEFAULT_THEME: &str = "libre-default";
pub const SYMBOL_PACKS: [&str; 2] = ["classic", "vivid"];
pub const DEFAULT_SYMBOLS: &str = "vivid";
pub const MOTIONS: [&str; 3] = ["auto", "full", "calm"];
pub const STILLS: [&str; 3] = ["auto", "png", "jpeg"];
pub const VIDEO_RANGES: [&str; 3] = ["auto", "limited", "full"];
pub const JPEG_LAYOUTS: [&str; 2] = ["4:4:4", "4:2:0"];
pub const JPEG_QUALITY: (i64, i64) = (50, 100);
/// The cards a slot shows where the config names none.
pub const DEFAULT_CARDS: [&str; 4] = ["playback", "ai", "forecast", "date"];
/// When the night begins and ends where a config says "sunset" or "sunrise" and the place is
/// not known.
pub const SUN_TIMES: [(&str, &str); 2] = [("sunset", "19:00"), ("sunrise", "07:00")];
/// The accent that follows the system's.
pub const ACCENT_SYSTEM: &str = "system";
pub const HARDWARE_NAME_LENGTH: usize = 100;
pub const FAN_LOOKS: [&str; 4] = ["noctua-g2", "noctua-a12x25", "noctua-a14", "libre"];
pub const FAN_PAINTS: [&str; 2] = ["theme", "own"];
/// The switches of every theme (a switch set outside a theme's table).
pub const EVERY_THEME: &str = "*";
pub const TRANSITION_OCCASIONS: [&str; 7] = [
    "game",
    "media",
    "idle",
    "sequence",
    "autopilot",
    "report",
    "manual",
];
/// The transition of each occasion where the config names none ("": the default's).
pub const OCCASION_TRANSITIONS: [(&str, &str); 7] = [
    ("game", ""),
    ("media", "entrance"),
    ("idle", "fade"),
    ("sequence", "light"),
    ("autopilot", ""),
    ("report", ""),
    ("manual", ""),
];
pub const TRANSITION_SPEEDS: [(&str, f64); 3] = [("fast", 0.6), ("normal", 1.0), ("slow", 1.6)];
pub const RANDOM_TRANSITION: &str = "random";
pub const RANDOM_TRANSITIONS: [&str; 8] = [
    "fade", "wipe", "zoom", "iris", "blinds", "dissolve", "light", "tiles",
];
/// The panels a config can name beside `[device]`.
pub const MORE_PANELS: usize = 3;
pub const ALARM_THEME: &str = "lumen-companion";
pub const MAX_SCENES: usize = 12;
pub const SCENE_NAME_LENGTH: usize = 40;
/// How far from the first panel another may be placed, in millimetres.
pub const ARRANGEMENT_MM: i64 = 5000;
// (devices/h264.py's choices: what `[video]` may name)
pub const X264_PRESETS: [&str; 6] = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
];
pub const PROFILES: [&str; 4] = ["auto", "baseline", "main", "high"];
pub const ENCODERS: [&str; 6] = [
    "auto",
    "libx264",
    "native",
    "h264_nvenc",
    "h264_amf",
    "h264_qsv",
];
pub const ENGINE_ENCODERS: [&str; 3] = ["auto", "x264", "card"];
pub const PIXELS: [&str; 3] = ["auto", "rgb", "card"];
/// The sources of the configs Libre Panel once wrote: read as "the defaults", which have grown.
const OLD_DEFAULT_PROVIDERS: [&str; 1] = ["psutil"];
const EARLIER_LINUX_PROVIDERS: [&str; 3] = ["load", "audio", "psutil"];

/// What reading a config raises in Python: the exception's class (ConfigError for what is
/// wrong with a setting; OverflowError, ValueError, RecursionError and the like for what
/// config.py lets through) and its message (`str(e)`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub class: &'static str,
    pub msg: String,
}

impl Error {
    pub fn new(class: &'static str, msg: impl Into<String>) -> Error {
        Error {
            class,
            msg: msg.into(),
        }
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.write_str(&self.msg)
    }
}

impl std::error::Error for Error {}

type Result<T> = std::result::Result<T, Error>;

/// ConfigError: what is wrong with a setting, by its name.
fn config_error(msg: impl Into<String>) -> Error {
    Error::new("ConfigError", msg)
}

/// `sys.platform` of the system this runs on: what the default sensor sources depend on.
pub fn platform() -> &'static str {
    if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "linux"
    }
}

/// `[video]`: how frames go to a panel that plays video.
#[derive(Clone, Debug, PartialEq)]
pub struct VideoConfig {
    pub mode: String,
    pub fps: i64,
    pub device_fps: i64,
    pub crf: i64,
    pub preset: String,
    pub keyframe_s: f64,
    pub maxrate: String,
    pub profile: String,
    pub encoder: String,
    pub pixels: String,
    pub engine_sends: bool,
    pub engine_layers: bool,
    pub engine_encoder: String,
    pub direct: bool,
    pub rest: bool,
    pub ffmpeg: String,
    pub local_clip: String,
    pub clip_asked: bool,
}

impl Default for VideoConfig {
    fn default() -> VideoConfig {
        VideoConfig {
            mode: "auto".into(),
            fps: 25,
            device_fps: 60,
            crf: 20,
            preset: "superfast".into(),
            keyframe_s: 10.0,
            maxrate: "auto".into(),
            profile: "auto".into(),
            encoder: "auto".into(),
            pixels: "auto".into(),
            engine_sends: true,
            engine_layers: true,
            engine_encoder: "auto".into(),
            direct: false,
            rest: true,
            ffmpeg: String::new(),
            local_clip: String::new(),
            clip_asked: false,
        }
    }
}

/// `[device]`, and the device of each of `[[panels]]`.
#[derive(Clone, Debug, PartialEq)]
pub struct DeviceConfig {
    pub model: String,
    pub driver: String,
    pub brightness: i64,
    pub rotate: i64,
    pub orientation: String,
    pub motion: String,
    pub still: String,
    pub video_range: String,
    pub jpeg: String,
    pub jpeg_quality: i64,
    pub dark_on_quit: bool,
    pub dark_on_sleep: bool,
    pub night: bool,
    pub night_from: String,
    pub night_to: String,
    pub night_brightness: i64,
    pub dark_when_away: bool,
    pub away_brightness: i64,
    pub output: String,
    pub port: String,
    /// Where the panel stands beside the others: x and y in millimetres.
    pub at: Option<(i64, i64)>,
    pub video: VideoConfig,
    /// The models of the other panels of this config.
    pub beside: Vec<String>,
}

impl Default for DeviceConfig {
    fn default() -> DeviceConfig {
        DeviceConfig {
            model: "auto".into(),
            driver: "auto".into(),
            brightness: 60,
            rotate: 0,
            orientation: String::new(),
            motion: "auto".into(),
            still: "auto".into(),
            video_range: "auto".into(),
            jpeg: "4:4:4".into(),
            jpeg_quality: 90,
            dark_on_quit: false,
            dark_on_sleep: true,
            night: false,
            night_from: "22:00".into(),
            night_to: "07:00".into(),
            night_brightness: 10,
            dark_when_away: true,
            away_brightness: 0,
            output: "libre-panel-frame.png".into(),
            port: String::new(),
            at: None,
            video: VideoConfig::default(),
            beside: Vec::new(),
        }
    }
}

/// `[sensors]`: the sources, and each source's own table.
#[derive(Clone, Debug, PartialEq)]
pub struct SensorsConfig {
    pub providers: Vec<String>,
    pub options: IndexMap<String, Table>,
}

/// `[hardware]`: the names the user gave the parts, and how each fan is drawn.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct HardwareConfig {
    pub cpu_name: String,
    pub gpu_name: String,
    pub memory_name: String,
    pub fans: IndexMap<String, String>,
    pub fan_look: String,
    pub fan_paint: String,
    pub fan_looks: IndexMap<String, String>,
    pub fan_paints: IndexMap<String, String>,
}

/// `[weather]`.
#[derive(Clone, Debug, PartialEq)]
pub struct WeatherConfig {
    pub enabled: bool,
    pub provider: String,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub place: String,
    pub units: String,
    pub update_minutes: BigInt,
}

impl Default for WeatherConfig {
    fn default() -> WeatherConfig {
        WeatherConfig {
            enabled: false,
            provider: "open-meteo".into(),
            latitude: None,
            longitude: None,
            place: String::new(),
            units: "metric".into(),
            update_minutes: BigInt::from(15),
        }
    }
}

/// `[services]`: the services that are on, and each one's own table.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ServicesConfig {
    pub enabled: Vec<String>,
    pub options: IndexMap<String, Table>,
}

/// One of `[modes]`: what a mode (a game, a film, idling) shows and how fast.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct ModeConfig {
    pub fps: Option<i64>,
    pub theme: Option<String>,
    pub on: bool,
    pub minutes: Option<f64>,
}

/// `[transitions]`: how one page gives way to the next.
#[derive(Clone, Debug, PartialEq)]
pub struct TransitionsConfig {
    pub default: String,
    pub speed: String,
    pub occasions: IndexMap<String, String>,
    pub random: Vec<String>,
}

impl Default for TransitionsConfig {
    fn default() -> TransitionsConfig {
        TransitionsConfig {
            default: "fade".into(),
            speed: "normal".into(),
            occasions: OCCASION_TRANSITIONS
                .iter()
                .map(|(occasion, name)| (occasion.to_string(), name.to_string()))
                .collect(),
            random: RANDOM_TRANSITIONS.iter().map(|n| n.to_string()).collect(),
        }
    }
}

/// `[sequence]`: the themes shown one after the other.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SequenceConfig {
    pub on: bool,
    pub steps: Vec<Step>,
}

impl Default for SequenceConfig {
    fn default() -> SequenceConfig {
        SequenceConfig {
            on: true,
            steps: Vec::new(),
        }
    }
}

/// One of `[[panels]]`: a panel beside the first.
#[derive(Clone, Debug, PartialEq)]
pub struct PanelConfig {
    pub theme: String,
    pub on: bool,
    pub device: DeviceConfig,
    pub follow_brightness: bool,
    pub brightness_offset: i64,
    pub game_theme: String,
    pub media_theme: String,
    pub alarm_theme: String,
    pub same_look: bool,
    pub follow: Option<bool>,
    pub video_mode: String,
    pub local_clip: String,
    pub clip_asked: bool,
}

impl Default for PanelConfig {
    fn default() -> PanelConfig {
        PanelConfig {
            theme: String::new(),
            on: true,
            device: DeviceConfig::default(),
            follow_brightness: true,
            brightness_offset: 0,
            game_theme: String::new(),
            media_theme: String::new(),
            alarm_theme: ALARM_THEME.into(),
            same_look: false,
            follow: None,
            video_mode: "off".into(),
            local_clip: String::new(),
            clip_asked: false,
        }
    }
}

/// One of `[[scenes]]`: a theme for each panel, under a name.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SceneConfig {
    pub name: String,
    pub themes: Vec<String>,
}

/// A config.toml, read and checked.
#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub theme: String,
    pub language: String,
    pub refresh_ms: Option<BigInt>,
    pub fps: i64,
    pub transition: String,
    pub transitions: TransitionsConfig,
    pub check_updates: Option<bool>,
    pub allow_notify: bool,
    pub device: DeviceConfig,
    pub sensors: SensorsConfig,
    pub hardware: HardwareConfig,
    pub weather: WeatherConfig,
    pub services: ServicesConfig,
    pub modes: IndexMap<String, ModeConfig>,
    pub sequence: SequenceConfig,
    pub panels: Vec<PanelConfig>,
    pub scenes: Vec<SceneConfig>,
    pub scene: String,
    pub hotkeys: IndexMap<String, String>,
    pub hotkeys_enabled: bool,
    pub symbols: String,
    pub cards: Vec<String>,
    pub card_order: String,
    pub card_transition: String,
    pub accent: String,
    pub card_opacity: i64,
    pub light_colours: bool,
    pub graph_steps: bool,
    pub across: String,
    pub switches: IndexMap<String, IndexMap<String, bool>>,
    pub same_look: bool,
    pub follow: Option<bool>,
    /// The file this was read from.
    pub path: Option<PathBuf>,
    /// When that file was last written, in nanoseconds: a later change is newer.
    pub stamp: Option<i128>,
    /// Whether the config names its theme (the default is not a choice).
    pub theme_given: bool,
}

impl Config {
    /// `Config(path=path)`: every default, on a system of this `platform` (`sys.platform`).
    pub fn new(path: Option<&Path>, platform: &str) -> Config {
        Config {
            theme: DEFAULT_THEME.into(),
            language: "auto".into(),
            refresh_ms: None,
            fps: 10,
            transition: "fade".into(),
            transitions: TransitionsConfig::default(),
            check_updates: None,
            allow_notify: false,
            device: DeviceConfig::default(),
            sensors: SensorsConfig {
                providers: default_providers(platform),
                options: IndexMap::new(),
            },
            hardware: HardwareConfig::default(),
            weather: WeatherConfig::default(),
            services: ServicesConfig::default(),
            modes: IndexMap::new(),
            sequence: SequenceConfig::default(),
            panels: Vec::new(),
            scenes: Vec::new(),
            scene: String::new(),
            hotkeys: IndexMap::new(),
            hotkeys_enabled: true,
            symbols: DEFAULT_SYMBOLS.into(),
            cards: DEFAULT_CARDS.iter().map(|c| c.to_string()).collect(),
            card_order: "first".into(),
            card_transition: "light".into(),
            accent: String::new(),
            card_opacity: 0,
            light_colours: false,
            graph_steps: false,
            across: String::new(),
            switches: IndexMap::new(),
            same_look: false,
            follow: None,
            path: path.map(Path::to_path_buf),
            stamp: None,
            theme_given: false,
        }
    }

    /// for_panel: the config as one of `[[panels]]` runs it: that panel's device and theme,
    /// the first panel's brightness and night where it follows them, and no sequence. None
    /// where there is no such panel.
    pub fn for_panel(&self, index: usize) -> Option<Config> {
        let panel = self.panels.get(index)?;
        let video = VideoConfig {
            mode: panel.video_mode.clone(),
            local_clip: panel.local_clip.clone(),
            clip_asked: panel.clip_asked,
            ..self.device.video.clone()
        };
        let others = self
            .panels
            .iter()
            .enumerate()
            .filter(|(n, _)| *n != index)
            .map(|(_, other)| &other.device);
        let beside = std::iter::once(&self.device)
            .chain(others)
            .filter(|other| other.model != "auto")
            .map(|other| other.model.clone())
            .collect();
        let mut device = DeviceConfig {
            video,
            beside,
            ..panel.device.clone()
        };
        if panel.follow_brightness {
            let first = &self.device;
            // (dark stays dark; what is lit stays lit, however far the offset goes)
            let shifted = |level: i64| match level {
                0 => 0,
                level => (level + panel.brightness_offset).clamp(1, 100),
            };
            device.brightness = shifted(first.brightness);
            device.night = first.night;
            device.night_from = first.night_from.clone();
            device.night_to = first.night_to.clone();
            device.night_brightness = shifted(first.night_brightness);
            device.dark_when_away = first.dark_when_away;
            device.away_brightness = shifted(first.away_brightness);
        }
        let pages = [
            ("game", &panel.game_theme),
            ("media", &panel.media_theme),
            ("alarm", &panel.alarm_theme),
        ];
        let modes = pages
            .into_iter()
            .filter(|(_, theme)| !theme.is_empty())
            .map(|(mode, theme)| {
                let page = ModeConfig {
                    theme: Some(theme.clone()),
                    on: true,
                    ..ModeConfig::default()
                };
                (mode.to_string(), page)
            })
            .collect();
        Some(Config {
            device,
            theme: match panel.theme.as_str() {
                "" => DEFAULT_THEME.to_string(),
                theme => theme.to_string(),
            },
            theme_given: !panel.theme.is_empty(),
            sequence: SequenceConfig {
                on: false,
                steps: Vec::new(),
            },
            panels: Vec::new(),
            modes,
            same_look: panel.same_look,
            follow: panel.follow,
            ..self.clone()
        })
    }
}

/// default_providers: the sensor sources of a config that names none, in order of priority:
/// the fast load source; the volume where it can be read (Windows and Linux); on Windows
/// LibreHardwareMonitor and PresentMon; on Linux `linux`, which reads the kernel and NVIDIA's
/// driver; psutil.
pub fn default_providers(platform: &str) -> Vec<String> {
    let linux = platform.starts_with("linux");
    let mut sources = vec!["load"];
    if platform == "win32" || linux {
        sources.push("audio");
    }
    if platform == "win32" {
        sources.extend(["librehardwaremonitor", "presentmon"]);
    }
    if linux {
        sources.push("linux");
    }
    sources.push("psutil");
    sources.into_iter().map(str::to_string).collect()
}

/// time_of_day: the minutes after midnight of a time such as "22:00" or "7:30"; None for what
/// is no time of day. (Python's pattern reads any decimal digit where it says `\d`.)
pub fn time_of_day(text: &str) -> Option<i64> {
    let chars: Vec<char> = lp_py::strip(text).chars().collect();
    let (hour, minute) = match chars[..] {
        [h, ':', m1, m2] => (lp_py::decimal(h)?, (m1, m2)),
        [h1, h2, ':', m1, m2] => {
            let known = match h1 {
                '0' | '1' => true,
                '2' => ('0'..='3').contains(&h2),
                _ => false,
            };
            if !known {
                return None;
            }
            (lp_py::decimal(h1)? * 10 + lp_py::decimal(h2)?, (m1, m2))
        }
        _ => return None,
    };
    if !('0'..='5').contains(&minute.0) {
        return None;
    }
    let minute = lp_py::decimal(minute.0)? * 10 + lp_py::decimal(minute.1)?;
    Some(i64::from(hour * 60 + minute))
}

/// night_time: when a night begins or ends, as a config keeps it: "sunset", "sunrise", or a
/// time of day written "HH:MM"; None for what is neither.
pub fn night_time(text: &str) -> Option<String> {
    let word = lp_py::lower(lp_py::strip(text));
    if SUN_TIMES.iter().any(|(sun, _)| *sun == word) {
        return Some(word);
    }
    let minutes = time_of_day(text)?;
    Some(format!("{:02}:{:02}", minutes / 60, minutes % 60))
}

/// How `_expect` prints the kind it asked for.
const STR: &str = "<class 'str'>";
const INT: &str = "<class 'int'>";
const BOOL: &str = "<class 'bool'>";
const LIST: &str = "<class 'list'>";
const DICT: &str = "<class 'dict'>";
const NUMBER: &str = "(<class 'int'>, <class 'float'>)";

/// What `_expect` raises of a value of another kind. (A boolean is a whole number to Python:
/// it is told apart before the kind is looked at.)
fn unexpected(value: &Toml, kind: &str, name: &str) -> Error {
    let got = match value {
        Toml::Bool(_) => "a boolean",
        other => other.type_name(),
    };
    config_error(format!("{name}: expected {kind}, got {got}"))
}

fn str_of<'a>(value: &'a Toml, name: &str) -> Result<&'a str> {
    match value {
        Toml::Str(text) => Ok(text),
        other => Err(unexpected(other, STR, name)),
    }
}

fn int_of<'a>(value: &'a Toml, name: &str) -> Result<&'a BigInt> {
    match value {
        Toml::Int(number) => Ok(number),
        other => Err(unexpected(other, INT, name)),
    }
}

fn bool_of(value: &Toml, name: &str) -> Result<bool> {
    match value {
        Toml::Bool(on) => Ok(*on),
        other => Err(unexpected(other, BOOL, name)),
    }
}

fn list_of<'a>(value: &'a Toml, name: &str) -> Result<&'a [Toml]> {
    match value {
        Toml::Array(items) => Ok(items),
        other => Err(unexpected(other, LIST, name)),
    }
}

fn dict_of<'a>(value: &'a Toml, name: &str) -> Result<&'a Table> {
    match value {
        Toml::Table(table) => Ok(table),
        other => Err(unexpected(other, DICT, name)),
    }
}

/// `float(_expect(value, (int, float), name))`: a whole number too large for a float is the
/// OverflowError Python lets through.
fn number_of(value: &Toml, name: &str) -> Result<f64> {
    match value {
        Toml::Float(number) => Ok(*number),
        Toml::Int(number) => {
            lp_py::num::int_to_float(number).map_err(|e| Error::new(e.kind.name(), e.msg))
        }
        other => Err(unexpected(other, NUMBER, name)),
    }
}

/// `_expect(table.get(key, default), str, name)`.
fn text(table: &Table, key: &str, default: &str, name: &str) -> Result<String> {
    match table.get(key) {
        Some(value) => Ok(str_of(value, name)?.to_string()),
        None => Ok(default.to_string()),
    }
}

/// `_expect(table.get(key, default), bool, name)`.
fn flag(table: &Table, key: &str, default: bool, name: &str) -> Result<bool> {
    table.get(key).map_or(Ok(default), |v| bool_of(v, name))
}

/// `_expect(table.get(key, default), int, name)`, and that it is one of `low` to `high`:
/// `problem` says what it must be.
fn whole(
    table: &Table,
    key: &str,
    default: i64,
    (low, high): (i64, i64),
    name: &str,
    problem: &str,
) -> Result<i64> {
    let number = match table.get(key) {
        Some(value) => within(int_of(value, name)?, low, high),
        None => Some(default).filter(|n| (low..=high).contains(n)),
    };
    number.ok_or_else(|| config_error(format!("{name}: {problem}")))
}

/// A whole number if it is one of `low` to `high`.
fn within(number: &BigInt, low: i64, high: i64) -> Option<i64> {
    number.to_i64().filter(|n| (low..=high).contains(n))
}

/// `_expect(data.get(key, {}), dict, name)`.
fn table_of<'a>(data: &'a Table, key: &str, empty: &'a Table) -> Result<&'a Table> {
    data.get(key).map_or(Ok(empty), |v| dict_of(v, key))
}

/// `" ".join(text.split())`: the words of a text, a space between them.
fn words(text: &str) -> String {
    text.split(lp_py::isspace)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// A name as the patterns of config.py have it: a lower-case letter or a digit, then up to 63
/// more, `_` and `-` among them (and `.` where `dot`).
fn is_name(text: &str, dot: bool) -> bool {
    let rest = |c: char| {
        c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-' || (dot && c == '.')
    };
    let mut chars = text.chars();
    text.len() <= 64
        && chars
            .next()
            .is_some_and(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        && chars.all(rest)
}

/// What `re.match` of a pattern ending in `$` looks at: the text, but for one line end at its
/// end.
fn before_line_end(text: &str) -> &str {
    text.strip_suffix('\n').unwrap_or(text)
}

/// _SWITCH_NAME: the name of a theme or of a switch.
fn is_switch_name(text: &str) -> bool {
    is_name(before_line_end(text), false)
}

/// _MAXRATE: "auto", or a bit rate such as "2M", "1500k" or "2.5M".
fn is_maxrate(text: &str) -> bool {
    let text = before_line_end(text);
    if text == "auto" {
        return true;
    }
    let digits =
        |text: &str| text.len() - text.trim_start_matches(|c: char| c.is_ascii_digit()).len();
    let whole = digits(text);
    if whole == 0 {
        return false;
    }
    let mut rest = &text[whole..];
    if let Some(fraction) = rest.strip_prefix('.') {
        match digits(fraction) {
            0 => return false,
            n => rest = &fraction[n..],
        }
    }
    matches!(rest, "" | "k" | "K" | "M")
}

/// accent.py's HEX: a colour written "#E0457B".
fn is_hex_colour(text: &str) -> bool {
    text.len() == 7
        && text
            .strip_prefix('#')
            .is_some_and(|digits| digits.chars().all(|c| c.is_ascii_hexdigit()))
}

/// The names of a list as a message gives them.
fn listed(names: &[&str]) -> String {
    names.join(", ")
}

/// _transition_name: the name of a transition, of the app's or of a plugin's.
fn transition_name(value: &Toml, name: &str, empty: bool) -> Result<String> {
    let text = lp_py::strip(str_of(value, name)?);
    if (text.is_empty() && empty) || is_name(text, true) {
        return Ok(text.to_string());
    }
    Err(config_error(format!(
        "{name}: the name of a transition, such as fade, wipe or entrance"
    )))
}

/// _transitions: `[transitions]` into the config.
fn transitions(cfg: &mut Config, table: &Table) -> Result<()> {
    for (key, value) in table {
        let name = format!("transitions.{key}");
        match key.as_str() {
            "default" => {
                cfg.transitions.default = transition_name(value, &name, false)?;
                cfg.transition = cfg.transitions.default.clone();
            }
            "speed" => {
                cfg.transitions.speed = str_of(value, &name)?.to_string();
                let speed = cfg.transitions.speed.as_str();
                if !TRANSITION_SPEEDS.iter().any(|(known, _)| *known == speed) {
                    let speeds: Vec<&str> = TRANSITION_SPEEDS.iter().map(|(s, _)| *s).collect();
                    return Err(config_error(format!("{name}: one of {}", listed(&speeds))));
                }
            }
            "random" => {
                let names = list_of(value, &name)?;
                if names.is_empty() || names.len() > 32 {
                    return Err(config_error(format!("{name}: one to 32 transitions")));
                }
                cfg.transitions.random = names
                    .iter()
                    .map(|item| transition_name(item, &name, false))
                    .collect::<Result<_>>()?;
                if cfg
                    .transitions
                    .random
                    .iter()
                    .any(|n| n == RANDOM_TRANSITION)
                {
                    return Err(config_error(format!("{name}: random cannot pick itself")));
                }
            }
            occasion if TRANSITION_OCCASIONS.contains(&occasion) => {
                let chosen = transition_name(value, &name, true)?;
                cfg.transitions.occasions.insert(key.clone(), chosen);
            }
            _ => {
                return Err(config_error(format!(
                    "unknown setting {}",
                    lp_py::repr(&name)
                )))
            }
        }
    }
    Ok(())
}

/// hardware_name: a name the user gave a part: its words, without what cannot be shown.
pub fn hardware_name(value: &Toml, name: &str) -> Result<String> {
    // (unicodedata's category Cc: the control characters)
    let control = |c: char| matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}');
    let text: String = words(str_of(value, name)?)
        .chars()
        .filter(|c| !control(*c))
        .collect();
    if text.chars().count() > HARDWARE_NAME_LENGTH {
        return Err(config_error(format!(
            "{name}: at most {HARDWARE_NAME_LENGTH} characters"
        )));
    }
    Ok(text)
}

/// `hardware_name(table.get(key, ""), name)`.
fn part_name(table: &Table, key: &str, name: &str) -> Result<String> {
    table
        .get(key)
        .map_or(Ok(String::new()), |v| hardware_name(v, name))
}

/// `_choice(table.get(key, ""), choices, name)`: one of the choices, or nothing.
fn choice(table: &Table, key: &str, choices: &[&str], name: &str) -> Result<String> {
    let Some(value) = table.get(key) else {
        return Ok(String::new());
    };
    let text = lp_py::strip(str_of(value, name)?);
    if !text.is_empty() && !choices.contains(&text) {
        return Err(config_error(format!(
            "{name}: one of {} (or empty)",
            listed(choices)
        )));
    }
    Ok(text.to_string())
}

type Names = IndexMap<String, String>;

/// _fans: `[[hardware.fans]]` as the names, the looks and the paints of the fans, each by
/// its sensor.
fn fans(value: Option<&Toml>) -> Result<(Names, Names, Names)> {
    let (mut names, mut looks, mut paints) = (Names::new(), Names::new(), Names::new());
    let tables = value.map_or(Ok(&[][..]), |v| list_of(v, "hardware.fans"))?;
    for (n, table) in tables.iter().enumerate() {
        let at = format!("hardware.fans[{n}]");
        let table = dict_of(table, &at)?;
        let sensor = text(table, "sensor", "", &format!("{at}.sensor"))?;
        let sensor = lp_py::strip(&sensor);
        if sensor.is_empty() {
            return Err(config_error(format!(
                "{at}.sensor: the key of the fan's sensor, e.g. fan.lpc.fan_1"
            )));
        }
        let name = part_name(table, "name", &format!("{at}.name"))?;
        let look = choice(table, "look", &FAN_LOOKS, &format!("{at}.look"))?;
        let paint = choice(table, "paint", &FAN_PAINTS, &format!("{at}.paint"))?;
        for (kept, given) in [(&mut names, name), (&mut looks, look), (&mut paints, paint)] {
            if !given.is_empty() {
                kept.insert(sensor.to_string(), given);
            }
        }
    }
    Ok((names, looks, paints))
}

/// _switches: `[switches]`: what a theme's switches are set to; a switch outside a theme's
/// table is every theme's.
fn switches(value: &Toml) -> Result<IndexMap<String, IndexMap<String, bool>>> {
    let mut chosen: IndexMap<String, IndexMap<String, bool>> = IndexMap::new();
    for (theme, table) in dict_of(value, "switches")? {
        if !is_switch_name(theme) {
            return Err(config_error(format!(
                "switches: {} is not the name of a theme or a switch",
                lp_py::repr(theme)
            )));
        }
        if let Toml::Bool(on) = table {
            let every = chosen.entry(EVERY_THEME.to_string()).or_default();
            every.insert(theme.clone(), *on);
            continue;
        }
        let at = format!("switches.{theme}");
        for (name, on) in dict_of(table, &at)? {
            if !is_switch_name(name) {
                return Err(config_error(format!(
                    "{at}: {} is not the name of a switch",
                    lp_py::repr(name)
                )));
            }
            let on = bool_of(on, &format!("{at}.{name}"))?;
            chosen
                .entry(theme.clone())
                .or_default()
                .insert(name.clone(), on);
        }
    }
    Ok(chosen)
}

/// _hotkeys: `[hotkeys]` (but for `enabled`): each action's combination, as it is written.
fn hotkeys_of(table: &Table) -> Result<IndexMap<String, String>> {
    let mut found: IndexMap<String, String> = IndexMap::new();
    for (action, value) in table.iter().filter(|(key, _)| *key != "enabled") {
        if !hotkeys::ACTIONS.contains(&action.as_str()) {
            return Err(config_error(format!(
                "hotkeys.{action}: none of {}",
                listed(&hotkeys::ACTIONS)
            )));
        }
        let keys = str_of(value, &format!("hotkeys.{action}"))?;
        if keys.is_empty() {
            continue;
        }
        let combo = match hotkeys::parse(keys) {
            Ok(combo) => combo.to_string(),
            Err(e) if e.class == "ValueError" => {
                return Err(config_error(format!("hotkeys.{action}: {}", e.msg)))
            }
            Err(e) => return Err(e),
        };
        if let Some((twin, _)) = found.iter().find(|(_, given)| **given == combo) {
            return Err(config_error(format!(
                "hotkeys.{action}: {combo} is the shortcut of {twin} already"
            )));
        }
        found.insert(action.clone(), combo);
    }
    Ok(found)
}

/// card_choice: the cards a config names (`cards`): each a card's name, with the time of its
/// turn if it has one (`ai for 5m`), none twice.
pub fn card_choice(value: &Toml, at: &str) -> Result<Vec<String>> {
    let examples = listed(&DEFAULT_CARDS);
    let mut cards: Vec<String> = Vec::new();
    let mut names: Vec<String> = Vec::new();
    for line in list_of(value, at)? {
        let line = words(str_of(line, &format!("{at}[]"))?);
        let card = slots::card_line(&line).filter(|c| !line.contains('#') && c.rule.is_empty());
        let Some(card) = card else {
            let name = line
                .split_once(" for ")
                .map_or(line.as_str(), |(name, _)| name);
            return Err(config_error(format!(
                "{at}: {} is no card (e.g. {examples})",
                lp_py::repr(name)
            )));
        };
        if line.contains(" for ") && card.seconds.is_none() {
            return Err(config_error(format!(
                "{at}: {}: a time such as 5m, 90s or 1h, {} s to {} h",
                lp_py::repr(&line),
                slots::TURN_S.0,
                slots::TURN_S.1 / 3600.0
            )));
        }
        if names.contains(&card.name) {
            return Err(config_error(format!(
                "{at}: {} is named twice",
                lp_py::repr(&card.name)
            )));
        }
        cards.push(line);
        names.push(card.name);
    }
    if cards.is_empty() {
        return Err(config_error(format!(
            "{at}: one card at least (e.g. {examples})"
        )));
    }
    Ok(cards)
}

/// place_at: where a panel stands (`at = [x, y]`); nothing, or an empty list, is no place.
pub fn place_at(value: Option<&Toml>, name: &str) -> Result<Option<(i64, i64)>> {
    let wrong = || {
        config_error(format!(
            "{name}: [x, y] in whole millimetres, -{ARRANGEMENT_MM} to {ARRANGEMENT_MM}"
        ))
    };
    let millimetres = |value: &Toml| match value {
        Toml::Int(number) => within(number, -ARRANGEMENT_MM, ARRANGEMENT_MM),
        _ => None,
    };
    match value {
        None => Ok(None),
        Some(Toml::Array(place)) => match &place[..] {
            [] => Ok(None),
            [x, y] => match (millimetres(x), millimetres(y)) {
                (Some(x), Some(y)) => Ok(Some((x, y))),
                _ => Err(wrong()),
            },
            _ => Err(wrong()),
        },
        Some(_) => Err(wrong()),
    }
}

/// _parse_device: a device's table; `[[panels]]` have a driver and an output of their own
/// where they name none (panel_defaults).
fn parse_device(dev: &Table, at: &str, driver: &str, output: &str) -> Result<DeviceConfig> {
    let mut device = DeviceConfig::default();
    let one_of = |key: &str, default: &str, allowed: &[&str], problem: &str| {
        let name = format!("{at}.{key}");
        let value = text(dev, key, default, &name)?;
        if !allowed.contains(&value.as_str()) {
            return Err(config_error(format!("{name}: {problem}")));
        }
        Ok(value)
    };
    device.model = text(dev, "model", &device.model, &format!("{at}.model"))?;
    if device.model != "auto" && lp_devices::models::find_model(&device.model).is_none() {
        return Err(config_error(format!(
            "{at}.model: unknown panel {} (see: libre-panel models)",
            lp_py::repr(&device.model)
        )));
    }
    device.driver = text(dev, "driver", driver, &format!("{at}.driver"))?;
    device.brightness = whole(
        dev,
        "brightness",
        device.brightness,
        (0, 100),
        &format!("{at}.brightness"),
        "must be between 0 and 100",
    )?;
    device.rotate = whole(
        dev,
        "rotate",
        device.rotate,
        (0, 180),
        &format!("{at}.rotate"),
        "must be 0, or 180 for upside down",
    )?;
    if device.rotate != 0 && device.rotate != 180 {
        return Err(config_error(format!(
            "{at}.rotate: must be 0, or 180 for upside down"
        )));
    }
    device.orientation = one_of(
        "orientation",
        &device.orientation,
        &["", "landscape", "portrait"],
        r#""landscape", "portrait" or """#,
    )?;
    device.motion = one_of(
        "motion",
        &device.motion,
        &MOTIONS,
        r#""auto", "full" or "calm""#,
    )?;
    device.still = one_of(
        "still",
        &device.still,
        &STILLS,
        r#""auto", "png" or "jpeg""#,
    )?;
    device.video_range = one_of(
        "video_range",
        &device.video_range,
        &VIDEO_RANGES,
        r#""auto", "limited" or "full""#,
    )?;
    device.jpeg = one_of("jpeg", &device.jpeg, &JPEG_LAYOUTS, r#""4:4:4" or "4:2:0""#)?;
    device.jpeg_quality = whole(
        dev,
        "jpeg_quality",
        device.jpeg_quality,
        JPEG_QUALITY,
        &format!("{at}.jpeg_quality"),
        &format!("must be between {} and {}", JPEG_QUALITY.0, JPEG_QUALITY.1),
    )?;
    let on = |key: &str, default: bool| flag(dev, key, default, &format!("{at}.{key}"));
    device.dark_on_quit = on("dark_on_quit", device.dark_on_quit)?;
    device.dark_on_sleep = on("dark_on_sleep", device.dark_on_sleep)?;
    device.night = on("night", device.night)?;
    device.dark_when_away = on("dark_when_away", device.dark_when_away)?;
    let time = |key: &str, default: &str| {
        let name = format!("{at}.{key}");
        night_time(&text(dev, key, default, &name)?).ok_or_else(|| {
            config_error(format!(
                r#"{name}: a time of day such as "22:00", or "sunset""#
            ))
        })
    };
    device.night_from = time("night_from", &device.night_from)?;
    device.night_to = time("night_to", &device.night_to)?;
    device.night_brightness = whole(
        dev,
        "night_brightness",
        device.night_brightness,
        (0, 100),
        &format!("{at}.night_brightness"),
        "0 to 100",
    )?;
    device.away_brightness = whole(
        dev,
        "away_brightness",
        device.away_brightness,
        (0, 100),
        &format!("{at}.away_brightness"),
        "0 to 100",
    )?;
    device.output = text(dev, "output", output, &format!("{at}.output"))?;
    let port = text(dev, "port", &device.port, &format!("{at}.port"))?;
    device.port = lp_py::strip(&port).to_string();
    device.at = place_at(dev.get("at"), &format!("{at}.at"))?;
    Ok(device)
}

/// _parse_video: `[video]`.
fn parse_video(v: &Table) -> Result<VideoConfig> {
    let mut video = VideoConfig::default();
    let one_of = |key: &str, default: &str, allowed: &[&str]| {
        let value = text(v, key, default, &format!("video.{key}"))?;
        if !allowed.contains(&value.as_str()) {
            return Err(config_error(format!(
                "video.{key}: must be one of {}",
                listed(allowed)
            )));
        }
        Ok(value)
    };
    video.mode = text(v, "mode", &video.mode, "video.mode")?;
    if !["auto", "off", "on"].contains(&video.mode.as_str()) {
        return Err(config_error(r#"video.mode: must be "auto", "off" or "on""#));
    }
    video.fps = whole(
        v,
        "fps",
        video.fps,
        (1, 60),
        "video.fps",
        "must be between 1 and 60",
    )?;
    video.device_fps = whole(
        v,
        "device_fps",
        video.device_fps,
        (video.fps, 120),
        "video.device_fps",
        "must be at least video.fps and at most 120",
    )?;
    video.crf = whole(
        v,
        "crf",
        video.crf,
        (0, 51),
        "video.crf",
        "must be between 0 and 51",
    )?;
    video.preset = one_of("preset", &video.preset, &X264_PRESETS)?;
    video.profile = one_of("profile", &video.profile, &PROFILES)?;
    video.encoder = one_of("encoder", &video.encoder, &ENCODERS)?;
    video.engine_sends = flag(v, "engine_sends", video.engine_sends, "video.engine_sends")?;
    video.engine_layers = flag(
        v,
        "engine_layers",
        video.engine_layers,
        "video.engine_layers",
    )?;
    video.engine_encoder = one_of("engine_encoder", &video.engine_encoder, &ENGINE_ENCODERS)?;
    video.direct = flag(v, "direct", video.direct, "video.direct")?;
    video.rest = flag(v, "rest", video.rest, "video.rest")?;
    video.pixels = one_of("pixels", &video.pixels, &PIXELS)?;
    if let Some(seconds) = v.get("keyframe_s") {
        video.keyframe_s = number_of(seconds, "video.keyframe_s")?;
    }
    // (no number is between them if it is no number)
    if !(1.0 <= video.keyframe_s && video.keyframe_s <= 60.0) {
        return Err(config_error(
            "video.keyframe_s: must be between 1 and 60 seconds",
        ));
    }
    video.maxrate = text(v, "maxrate", &video.maxrate, "video.maxrate")?;
    if !is_maxrate(&video.maxrate) {
        return Err(config_error(
            r#"video.maxrate: "auto", or a bit rate such as "2M" or "1500k""#,
        ));
    }
    video.ffmpeg = text(v, "ffmpeg", &video.ffmpeg, "video.ffmpeg")?;
    video.local_clip = text(v, "local_clip", &video.local_clip, "video.local_clip")?;
    if video.local_clip.len() > 200 {
        return Err(config_error("video.local_clip: at most 200 bytes"));
    }
    video.clip_asked = flag(v, "clip_asked", video.clip_asked, "video.clip_asked")?;
    Ok(video)
}

/// _panel_video: a panel's own `video` table: its mode, clip and whether the clip was asked
/// about.
fn panel_video(value: Option<&Toml>, at: &str) -> Result<(String, String, bool)> {
    let empty = Table::new();
    let table = value.map_or(Ok(&empty), |v| dict_of(v, at))?;
    let mode = text(table, "mode", "off", &format!("{at}.mode"))?;
    if !["auto", "off", "on"].contains(&mode.as_str()) {
        return Err(config_error(format!(
            r#"{at}.mode: must be "auto", "off" or "on""#
        )));
    }
    let clip = text(table, "local_clip", "", &format!("{at}.local_clip"))?;
    if clip.len() > 200 {
        return Err(config_error(format!("{at}.local_clip: at most 200 bytes")));
    }
    let asked = flag(table, "clip_asked", false, &format!("{at}.clip_asked"))?;
    let known = ["mode", "local_clip", "clip_asked"];
    if let Some(unknown) = table.keys().filter(|k| !known.contains(&k.as_str())).min() {
        return Err(config_error(format!(
            "{at}: unknown key {} ({})",
            lp_py::repr(unknown),
            listed(&known)
        )));
    }
    Ok((mode, clip, asked))
}

/// _panels: `[[panels]]`: the panels beside the first.
fn panels(value: Option<&Toml>) -> Result<Vec<PanelConfig>> {
    let tables = value.map_or(Ok(&[][..]), |v| list_of(v, "panels"))?;
    if tables.len() > MORE_PANELS {
        return Err(config_error(format!(
            "panels: at most {MORE_PANELS} panels beside [device]"
        )));
    }
    let mut found = Vec::new();
    for (n, table) in tables.iter().enumerate() {
        let at = format!("panels[{n}]");
        let table = dict_of(table, &at)?;
        let stripped = |key: &str, default: &str| {
            let value = text(table, key, default, &format!("{at}.{key}"))?;
            Ok::<_, Error>(lp_py::strip(&value).to_string())
        };
        let on = |key: &str, default: bool| flag(table, key, default, &format!("{at}.{key}"));
        let theme = stripped("theme", "")?;
        let shown = on("on", true)?;
        let follow_brightness = on("follow_brightness", true)?;
        let game_theme = stripped("game_theme", "")?;
        let media_theme = stripped("media_theme", "")?;
        let alarm_theme = stripped("alarm_theme", ALARM_THEME)?;
        let same_look = on("same_look", false)?;
        let follow = table
            .get("follow")
            .map(|v| bool_of(v, &format!("{at}.follow")))
            .transpose()?;
        let (video_mode, local_clip, clip_asked) =
            panel_video(table.get("video"), &format!("{at}.video"))?;
        let brightness_offset = whole(
            table,
            "brightness_offset",
            0,
            (-50, 50),
            &format!("{at}.brightness_offset"),
            "-50 to 50",
        )?;
        let output = format!("libre-panel-frame-{}.png", n + 2);
        found.push(PanelConfig {
            theme,
            on: shown,
            device: parse_device(table, &at, "turzx", &output)?,
            follow_brightness,
            brightness_offset,
            game_theme,
            media_theme,
            alarm_theme,
            same_look,
            follow,
            video_mode,
            local_clip,
            clip_asked,
        });
    }
    Ok(found)
}

/// _scenes: `[[scenes]]`.
fn scenes(value: Option<&Toml>) -> Result<Vec<SceneConfig>> {
    let tables = value.map_or(Ok(&[][..]), |v| list_of(v, "scenes"))?;
    if tables.len() > MAX_SCENES {
        return Err(config_error(format!("scenes: at most {MAX_SCENES}")));
    }
    let mut found: Vec<SceneConfig> = Vec::new();
    for (n, table) in tables.iter().enumerate() {
        let at = format!("scenes[{n}]");
        let table = dict_of(table, &at)?;
        let name = words(&text(table, "name", "", &format!("{at}.name"))?);
        if name.is_empty() || name.chars().count() > SCENE_NAME_LENGTH {
            return Err(config_error(format!(
                "{at}.name: 1 to {SCENE_NAME_LENGTH} characters"
            )));
        }
        if found.iter().any(|scene| scene.name == name) {
            return Err(config_error(format!(
                "{at}.name: {} is there twice",
                lp_py::repr(&name)
            )));
        }
        let themes = table
            .get("themes")
            .map_or(Ok(&[][..]), |v| list_of(v, &format!("{at}.themes")))?;
        if themes.is_empty() || themes.len() > 1 + MORE_PANELS {
            return Err(config_error(format!(
                "{at}.themes: 1 to {} themes",
                1 + MORE_PANELS
            )));
        }
        let themes = themes
            .iter()
            .map(|theme| Ok(lp_py::strip(str_of(theme, &format!("{at}.themes[]"))?).to_string()))
            .collect::<Result<_>>()?;
        found.push(SceneConfig { name, themes });
    }
    Ok(found)
}

/// _put_on_scene: the themes of the scene the config is on, over the panels' own.
fn put_on_scene(cfg: &mut Config) {
    let Some(scene) = cfg.scenes.iter().find(|scene| scene.name == cfg.scene) else {
        return;
    };
    if let Some(first) = scene.themes.first().filter(|theme| !theme.is_empty()) {
        cfg.theme = first.clone();
        cfg.theme_given = true;
    }
    for (panel, theme) in cfg.panels.iter_mut().zip(scene.themes.iter().skip(1)) {
        if !theme.is_empty() {
            panel.theme = theme.clone();
        }
    }
}

/// The tables of a table (a source's or a service's own settings), but for `but`.
fn tables_of(table: &Table, but: &str) -> IndexMap<String, Table> {
    table
        .iter()
        .filter(|(key, _)| *key != but)
        .filter_map(|(key, value)| match value {
            Toml::Table(own) => Some((key.clone(), own.clone())),
            _ => None,
        })
        .collect()
}

/// parse_config: a config.toml's tables as a Config, on a system of this `platform`
/// (`sys.platform`); the error says which setting is wrong and why. What an old config says
/// of a setting that is no more is let be.
pub fn parse_config(data: &Table, path: Option<&Path>, platform: &str) -> Result<Config> {
    let mut cfg = Config::new(path, platform);
    let empty = Table::new();
    if let Some(value) = data.get("theme") {
        cfg.theme = str_of(value, "theme")?.to_string();
        cfg.theme_given = true;
    }
    if let Some(value) = data.get("language") {
        cfg.language = str_of(value, "language")?.to_string();
        let known = |code: &str| lp_i18n::LANGUAGES.iter().any(|(known, _)| *known == code);
        if cfg.language != "auto" && !known(&cfg.language) {
            let codes: Vec<&str> = lp_i18n::LANGUAGES.iter().map(|(code, _)| *code).collect();
            return Err(config_error(format!(
                "language: must be auto or one of {}",
                listed(&codes)
            )));
        }
    }
    cfg.fps = whole(
        data,
        "fps",
        cfg.fps,
        (1, 60),
        "fps",
        "must be between 1 and 60",
    )?;
    if let Some(value) = data.get("transition") {
        cfg.transitions.default = transition_name(value, "transition", false)?;
        cfg.transition = cfg.transitions.default.clone();
    }
    transitions(&mut cfg, table_of(data, "transitions", &empty)?)?;
    if let Some(value) = data.get("symbols") {
        cfg.symbols = lp_py::strip(str_of(value, "symbols")?).to_string();
        if !SYMBOL_PACKS.contains(&cfg.symbols.as_str()) {
            return Err(config_error(format!(
                "symbols: must be one of {}",
                listed(&SYMBOL_PACKS)
            )));
        }
    }
    if let Some(value) = data.get("cards") {
        cfg.cards = card_choice(value, "cards")?;
    }
    for (key, allowed) in [
        ("card_order", slots::CARD_ORDERS),
        ("card_transition", slots::CARD_TRANSITIONS),
    ] {
        let Some(value) = data.get(key) else {
            continue;
        };
        let value = lp_py::strip(str_of(value, key)?);
        if !allowed.contains(&value) {
            return Err(config_error(format!(
                "{key}: must be one of {}",
                listed(allowed)
            )));
        }
        match key {
            "card_order" => cfg.card_order = value.to_string(),
            _ => cfg.card_transition = value.to_string(),
        }
    }
    if let Some(value) = data.get("accent") {
        cfg.accent = lp_py::strip(str_of(value, "accent")?).to_string();
        if !cfg.accent.is_empty() && cfg.accent != ACCENT_SYSTEM && !is_hex_colour(&cfg.accent) {
            return Err(config_error(
                r##"accent: "system", a colour such as "#E0457B", or empty"##,
            ));
        }
    }
    if let Some(value) = data.get("card_opacity") {
        let opacity = int_of(value, "card_opacity")?;
        cfg.card_opacity = within(opacity, 0, 0)
            .or_else(|| within(opacity, 40, 100))
            .ok_or_else(|| {
                config_error("card_opacity: 0 (each theme's own) or between 40 and 100")
            })?;
    }
    cfg.light_colours = flag(data, "light_colours", cfg.light_colours, "light_colours")?;
    cfg.graph_steps = flag(data, "graph_steps", cfg.graph_steps, "graph_steps")?;
    if let Some(value) = data.get("across") {
        cfg.across = lp_py::strip(str_of(value, "across")?).to_string();
        if cfg.across.chars().count() > 300 {
            return Err(config_error("across: at most 300 characters"));
        }
    }
    if let Some(value) = data.get("refresh_ms") {
        let refresh = int_of(value, "refresh_ms")?;
        if *refresh < BigInt::from(100) {
            return Err(config_error("refresh_ms: must be at least 100"));
        }
        cfg.refresh_ms = Some(refresh.clone());
    }
    if let Some(value) = data.get("check_updates") {
        cfg.check_updates = Some(bool_of(value, "check_updates")?);
    }
    cfg.allow_notify = flag(data, "allow_notify", cfg.allow_notify, "allow_notify")?;
    if let Some(value) = data.get("switches") {
        cfg.switches = switches(value)?;
    }
    if let Some(value) = data.get("hotkeys") {
        let table = dict_of(value, "hotkeys")?;
        cfg.hotkeys_enabled = flag(table, "enabled", cfg.hotkeys_enabled, "hotkeys.enabled")?;
        cfg.hotkeys = hotkeys_of(table)?;
    }

    cfg.device = parse_device(
        table_of(data, "device", &empty)?,
        "device",
        "auto",
        "libre-panel-frame.png",
    )?;
    cfg.device.video = parse_video(table_of(data, "video", &empty)?)?;

    let sensors = table_of(data, "sensors", &empty)?;
    if let Some(providers) = sensors.get("providers") {
        cfg.sensors.providers = list_of(providers, "sensors.providers")?
            .iter()
            .map(|p| Ok(str_of(p, "sensors.providers[]")?.to_string()))
            .collect::<Result<_>>()?;
    }
    let given = |sources: &[&str]| cfg.sensors.providers.iter().eq(sources.iter());
    if given(&OLD_DEFAULT_PROVIDERS)
        || (platform.starts_with("linux") && given(&EARLIER_LINUX_PROVIDERS))
    {
        cfg.sensors.providers = default_providers(platform);
    }
    cfg.sensors.options = tables_of(sensors, "providers");

    let hw = table_of(data, "hardware", &empty)?;
    let (names, looks, paints) = fans(hw.get("fans"))?;
    cfg.hardware = HardwareConfig {
        cpu_name: part_name(hw, "cpu_name", "hardware.cpu_name")?,
        gpu_name: part_name(hw, "gpu_name", "hardware.gpu_name")?,
        memory_name: part_name(hw, "memory_name", "hardware.memory_name")?,
        fans: names,
        fan_look: choice(hw, "fan_look", &FAN_LOOKS, "hardware.fan_look")?,
        fan_paint: choice(hw, "fan_paint", &FAN_PAINTS, "hardware.fan_paint")?,
        fan_looks: looks,
        fan_paints: paints,
    };

    let w = table_of(data, "weather", &empty)?;
    cfg.weather.enabled = flag(w, "enabled", false, "weather.enabled")?;
    cfg.weather.provider = text(w, "provider", "open-meteo", "weather.provider")?;
    if let Some(value) = w.get("latitude") {
        cfg.weather.latitude = Some(number_of(value, "weather.latitude")?);
    }
    if let Some(value) = w.get("longitude") {
        cfg.weather.longitude = Some(number_of(value, "weather.longitude")?);
    }
    cfg.weather.place = text(w, "place", "", "weather.place")?;
    if cfg.weather.place.chars().count() > 120 {
        return Err(config_error("weather.place: at most 120 characters"));
    }
    cfg.weather.units = text(w, "units", "metric", "weather.units")?;
    if !["metric", "imperial"].contains(&cfg.weather.units.as_str()) {
        return Err(config_error(
            r#"weather.units: must be "metric" or "imperial""#,
        ));
    }
    if let Some(value) = w.get("update_minutes") {
        cfg.weather.update_minutes = int_of(value, "weather.update_minutes")?.clone();
    }
    let services = table_of(data, "services", &empty)?;
    if let Some(enabled) = services.get("enabled") {
        cfg.services.enabled = list_of(enabled, "services.enabled")?
            .iter()
            .map(|name| Ok(str_of(name, "services.enabled[]")?.to_string()))
            .collect::<Result<_>>()?;
    }
    cfg.services.options = tables_of(services, "enabled");

    for (name, table) in table_of(data, "modes", &empty)? {
        let at = format!("modes.{name}");
        let table = dict_of(table, &at)?;
        let mut mode = ModeConfig::default();
        if let Some(value) = table.get("fps") {
            let fps = int_of(value, &format!("{at}.fps"))?;
            mode.fps = Some(
                within(fps, 1, 60)
                    .ok_or_else(|| config_error(format!("{at}.fps: must be between 1 and 60")))?,
            );
        }
        if let Some(value) = table.get("theme") {
            mode.theme = Some(str_of(value, &format!("{at}.theme"))?.to_string());
        }
        mode.on = flag(table, "on", mode.on, &format!("{at}.on"))?;
        if let Some(value) = table.get("minutes") {
            let minutes = match value {
                Toml::Int(minutes) => within(minutes, 1, 1440).map(|m| m as f64),
                Toml::Float(minutes) => Some(*minutes).filter(|m| (0.5..=1440.0).contains(m)),
                _ => {
                    return Err(config_error(format!(
                        "{at}.minutes: expected a number of minutes"
                    )))
                }
            };
            mode.minutes = Some(minutes.ok_or_else(|| {
                config_error(format!("{at}.minutes: must be between 0.5 and 1440"))
            })?);
        }
        cfg.modes.insert(name.clone(), mode);
    }

    let sequence = table_of(data, "sequence", &empty)?;
    cfg.sequence.on = flag(sequence, "on", true, "sequence.on")?;
    if let Some(steps) = sequence.get("steps") {
        cfg.sequence.steps = match sequence::parse_steps(steps, "sequence.steps") {
            Err(e) if e.class == "StepError" => return Err(config_error(e.msg)),
            steps => steps?,
        };
    }

    cfg.panels = panels(data.get("panels"))?;
    cfg.device.beside = cfg
        .panels
        .iter()
        .filter(|panel| panel.device.model != "auto")
        .map(|panel| panel.device.model.clone())
        .collect();
    cfg.scenes = scenes(data.get("scenes"))?;
    cfg.scene = lp_py::strip(&text(data, "scene", "", "scene")?).to_string();
    put_on_scene(&mut cfg);

    if cfg.weather.enabled && (cfg.weather.latitude.is_none() || cfg.weather.longitude.is_none()) {
        return Err(config_error(
            "weather is enabled but no location is set: add weather.latitude and \
             weather.longitude (find them with: libre-panel location \"Your City\")",
        ));
    }
    Ok(cfg)
}

/// The class of the OSError Python raises of a file that cannot be read.
fn os_error(error: &std::io::Error) -> Error {
    use std::io::ErrorKind;
    let class = match error.kind() {
        ErrorKind::NotFound => "FileNotFoundError",
        ErrorKind::PermissionDenied => "PermissionError",
        ErrorKind::IsADirectory => "IsADirectoryError",
        _ => "OSError",
    };
    Error::new(class, error.to_string())
}

/// When a file was last written, in nanoseconds since 1970 (`st_mtime_ns`); None where it
/// cannot be asked.
fn stamp_of(path: &Path) -> Option<i128> {
    let written = std::fs::metadata(path).ok()?.modified().ok()?;
    Some(match written.duration_since(std::time::UNIX_EPOCH) {
        Ok(since) => since.as_nanos() as i128,
        Err(before) => -(before.duration().as_nanos() as i128),
    })
}

/// load_config: the config of a config.toml; a missing file means all defaults.
pub fn load_config(path: &Path, platform: &str) -> Result<Config> {
    load_config_at(path, platform, lp_py::toml::LOADS_FRAME)
}

/// load_config where Python's reader of the file would be called `frame` calls deep (see
/// lp_py::toml::loads_at: how deep a config may nest depends on it).
pub fn load_config_at(path: &Path, platform: &str, frame: usize) -> Result<Config> {
    // (before reading: a later change is newer)
    let Some(stamp) = stamp_of(path) else {
        return Ok(Config::new(Some(path), platform));
    };
    let bytes = std::fs::read(path).map_err(|e| os_error(&e))?;
    let text = lp_py::codecs::read_text(&bytes).map_err(|e| {
        config_error(format!(
            "{}: not UTF-8 text (byte {}); save it as UTF-8",
            path.display(),
            e.start
        ))
    })?;
    let data = lp_py::toml::loads_at(&text, frame).map_err(|e| match e.kind {
        lp_py::toml::ErrorKind::Decode => config_error(format!("{}: {}", path.display(), e.msg)),
        lp_py::toml::ErrorKind::Value => Error::new("ValueError", e.msg),
        lp_py::toml::ErrorKind::Recursion => Error::new("RecursionError", e.msg),
    })?;
    let mut config = parse_config(&data, Some(path), platform)?;
    config.stamp = Some(stamp);
    Ok(config)
}

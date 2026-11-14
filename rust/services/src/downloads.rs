//! Downloads in progress, Steam's and the browser's, as one state for a download card
//! (services/downloads.py).
//!
//! Steam: `logs/content_log.txt` (what Steam does with an app: its phases and size) and the
//! app's `appmanifest_<id>.acf` (its name and how much is loaded), both only read. Steam's
//! log names a rate once a minute at best, so the speed is the network's download
//! (`net.down`); when the manifest stands still although data flows, the loaded amount is
//! extrapolated (`estimated`).
//!
//! Browsers: the part files (`.crdownload`, `.part`, `.partial`) in the user's Downloads
//! folder; how a file grows gives the amount and the speed. The size of the whole would be
//! in the browser's history, which is never read (it holds everything one has looked at): a
//! browser download has no remaining time.
//!
//! Small is no download: nothing below `min_mb` is shown, and a download of unknown size
//! only after 5 s at more than 1 MB/s. A finished download stays for a few seconds as
//! `done`. Nothing is written and nothing is kept: the state lives on the panel only.

use std::collections::VecDeque;
use std::io::{Read, Seek, SeekFrom};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::regex::Regex;
use lp_py::{Dict, Value};
use num_bigint::BigInt;

use crate::host::ServiceHost;
use crate::manager::{Part, Schema, Service};
use crate::paths::{self, Path, HERE};
use crate::steam::{self, is_dir, is_file, Places};

/// the state this service publishes
pub const KEY: &str = "downloads";
/// how often it looks
const STEP_S: f64 = 1.0;
const MB: f64 = 1024.0 * 1024.0;
/// seconds of speed a state carries
const HISTORY_S: usize = 180;
/// a finished download stays this long
const DONE_S: f64 = 6.0;
/// a part file that did not grow this long is paused
const PAUSED_AFTER_S: f64 = 20.0;
/// Steam downloads, but no data flows and the log says nothing: hide it
const STEAM_QUIET_S: f64 = 60.0;
/// the log says "running", the manifest "installed": a left-over
const STEAM_STALE_S: f64 = 30.0;
/// at the start, so much of the end of Steam's log is read
const TAIL_BYTES: u64 = 256 * 1024;
/// Steam's libraries and the folder are looked up again after this
const PLACES_S: f64 = 300.0;
/// a download of unknown size counts after this long ...
const UNKNOWN_AFTER_S: f64 = 5.0;
/// ... at more than this speed
const UNKNOWN_RATE: f64 = MB;
/// no remaining time at a speed below this
const REMAINING_RATE: f64 = 0.2 * MB;
const SUFFIXES: [&str; 3] = [".crdownload", ".part", ".partial"];
/// StateFlags of a manifest: 256 an update is running, 512 it is paused, 1024 it was started
const STEAM_RUNNING: u32 = 256 | 512 | 1024;
const MANIFEST_KEYS: [&str; 3] = ["stateflags", "bytestodownload", "bytesdownloaded"];

/// What a download is doing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Downloading,
    Preparing,
    Paused,
    Installing,
    Done,
}

impl Phase {
    pub fn name(self) -> &'static str {
        match self {
            Phase::Downloading => "downloading",
            Phase::Preparing => "preparing",
            Phase::Paused => "paused",
            Phase::Installing => "installing",
            Phase::Done => "done",
        }
    }
}

/// What Steam's "App update changed : ..." says.
enum Said {
    Phase(Phase),
    /// the update is over
    Over,
    /// a transition
    Between,
}

fn said(state: &str) -> Said {
    let state = lp_py::strip(state);
    let has = |words: &[&str]| words.iter().any(|word| state.contains(word));
    if has(&["Downloading"]) {
        Said::Phase(Phase::Downloading)
    } else if has(&["Verifying Staged", "Committing", "Staging"]) {
        Said::Phase(Phase::Installing)
    } else if has(&["Preallocating", "Reconfiguring", "Verifying Installed"]) {
        Said::Phase(Phase::Preparing)
    } else if has(&["Paused", "Suspended"]) {
        Said::Phase(Phase::Paused)
    } else if state == "None" || state.is_empty() {
        Said::Over
    } else {
        Said::Between
    }
}

// -- where the browser puts what it loads -------------------------------------------------------

/// A folder the system knows by its use.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Known {
    Downloads,
    Pictures,
}

/// shell_downloads: the folder as the Windows shell knows it (the user may have moved it).
#[cfg(windows)]
fn shell_folder(known: Known) -> Option<String> {
    use windows_sys::Win32::System::Com::CoTaskMemFree;
    use windows_sys::Win32::UI::Shell::{
        FOLDERID_Downloads, FOLDERID_Pictures, SHGetKnownFolderPath,
    };
    let id = match known {
        Known::Downloads => FOLDERID_Downloads,
        Known::Pictures => FOLDERID_Pictures,
    };
    let mut out: *mut u16 = std::ptr::null_mut();
    // SAFETY: the shell writes a text ending with a zero that it allocated into `out`; it is
    // read up to that zero and freed as the shell asks.
    unsafe {
        let status = SHGetKnownFolderPath(&id, 0, std::ptr::null_mut(), &mut out);
        if out.is_null() {
            return None;
        }
        let mut length = 0;
        while *out.add(length) != 0 {
            length += 1;
        }
        let text = String::from_utf16_lossy(std::slice::from_raw_parts(out, length));
        CoTaskMemFree(out.cast());
        (status == 0).then_some(text)
    }
}

/// What `xdg-user-dir` says for a folder, where it answers within three seconds.
#[cfg(target_os = "linux")]
fn xdg_folder(known: Known) -> Option<String> {
    use std::process::{Command, Stdio};
    let name = match known {
        Known::Downloads => "DOWNLOAD",
        Known::Pictures => "PICTURES",
    };
    let mut child = Command::new("xdg-user-dir")
        .arg(name)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let until = Instant::now() + Duration::from_secs(3);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < until => std::thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    let mut raw = Vec::new();
    child.stdout.take()?.read_to_end(&mut raw).ok()?;
    if !status.success() {
        return None;
    }
    Some(lp_py::strip(std::str::from_utf8(&raw).ok()?).to_string())
}

/// The folder as this system knows it: on Windows the shell's known folder, on Linux what
/// `xdg-user-dir` says.
pub fn system_folder(known: Known) -> Option<String> {
    #[cfg(windows)]
    return shell_folder(known);
    #[cfg(target_os = "linux")]
    return xdg_folder(known);
    #[cfg(not(any(windows, target_os = "linux")))]
    {
        let _ = known;
        None
    }
}

/// The user's home folder (`Path.home()`).
pub fn home(places: &Places) -> Path {
    let homes = paths::Homes {
        env: &*places.env,
        user: &paths::user_home,
    };
    let found = paths::expanduser(HERE, "~", &homes);
    Path::new(HERE, if found == "~" { &places.home } else { &found })
}

/// What the system `found`, where it is a folder of its own.
fn own_folder(found: Option<String>, home: &Path) -> Option<Path> {
    let folder = Path::new(HERE, &found.filter(|found| !found.is_empty())?);
    let normal = |path: &Path| paths::folded(HERE, &path.text());
    // without a setting xdg-user-dir answers with the home folder itself: no folder of its own
    (folder.is_absolute() && normal(&folder) != normal(home)).then_some(folder)
}

/// downloads_folder: the user's Downloads folder: what the system `found`; failing that
/// `~/Downloads`. Nothing is created.
pub fn downloads_folder(found: Option<String>, home: &Path) -> Path {
    own_folder(found, home).unwrap_or_else(|| home.join("Downloads"))
}

/// pictures_folder: the user's Pictures folder: what the system `found`; failing that
/// `~/Pictures` where it is there, else `~/Documents`, else the home folder. Nothing is
/// created.
pub fn pictures_folder(found: Option<String>, home: &Path) -> Path {
    if let Some(folder) = own_folder(found, home) {
        return folder;
    }
    let named = ["Pictures", "Documents"].map(|name| home.join(name));
    (named.into_iter().find(is_dir)).unwrap_or_else(|| home.clone())
}

// -- what is going on ---------------------------------------------------------------------------

/// One download as a card shows it.
#[derive(Clone, Debug)]
struct Item {
    id: String,
    /// "steam" or "browser"
    source: &'static str,
    name: String,
    phase: Phase,
    loaded: f64,
    total: Option<f64>,
    rate: f64,
    remaining: Option<f64>,
    started: f64,
    estimated: bool,
    /// a finished one is shown until then
    until: f64,
}

/// An app Steam is working on.
struct App {
    name: String,
    started: f64,
    phase: Phase,
    total: f64,
    /// the manifest's amount, extrapolated with the speed in between
    loaded: f64,
    manifest_loaded: BigInt,
    manifest_at: f64,
    /// when the log last said something about it
    log_at: f64,
    /// when data last flowed
    moving_at: f64,
    shown: bool,
    done_until: f64,
}

/// A part file of a browser.
struct PartFile {
    id: String,
    name: String,
    started: f64,
    size: u64,
    /// when the size was read
    at: f64,
    moving_at: f64,
    rate: f64,
    shown: bool,
}

/// `int(value or 0)`, 0 where that fails.
fn whole(value: Option<&Value>) -> BigInt {
    match value {
        Some(Value::Str(text)) => lp_py::int_of_str(text).unwrap_or_default(),
        Some(Value::Int(n)) => n.clone(),
        Some(Value::Bool(on)) => BigInt::from(u8::from(*on)),
        _ => BigInt::default(),
    }
}

fn float(n: &BigInt) -> f64 {
    Value::Int(n.clone()).to_f64().unwrap_or(f64::INFINITY)
}

/// `round(x)` as a whole number; none where Python raises (no finite number).
fn rounded(x: f64) -> Result<Value, String> {
    if x.is_nan() {
        return Err("ValueError: cannot convert float NaN to integer".into());
    }
    if x.is_infinite() {
        return Err("OverflowError: cannot convert float infinity to integer".into());
    }
    let text = format!("{:.0}", lp_py::round(x));
    Ok(Value::Int(text.parse().unwrap_or_default()))
}

struct Patterns {
    start: Regex,
    changed: Regex,
    finished: Regex,
}

fn patterns() -> Patterns {
    let made = |pattern: &str| Regex::new(pattern, false).expect("a pattern that reads");
    Patterns {
        start: made(r"AppID (\d+) update started : download (\d+)/(\d+).*?stage (\d+)/(\d+)"),
        changed: made(r"AppID (\d+) App update changed : (.*)$"),
        finished: made(r"AppID (\d+) finished update"),
    }
}

/// How the Downloads folder is found where none is given.
pub type FindFolder = Box<dyn Fn() -> Path + Send>;

/// What the tracker is told to look at.
pub struct Settings {
    pub steam: bool,
    pub browser: bool,
    pub min_mb: f64,
    pub steam_path: String,
    pub folder: String,
}

/// Follows Steam's log and manifests and the browsers' part files. `step(now, rate)` is
/// called once a second with the network's download in bytes per second and gives the state
/// to publish: the download that matters most, or none.
pub struct Tracker {
    steam_on: bool,
    browser_on: bool,
    min_bytes: f64,
    steam_path: String,
    folder_given: String,
    find_folder: FindFolder,
    places: Places,
    /// what there is to say for the log
    pub notes: Vec<String>,
    pub root: Option<Path>,
    libraries: Vec<Path>,
    pub folder: Option<Path>,
    places_at: f64,
    warned: bool,
    log_pos: Option<u64>,
    apps: IndexMap<String, App>,
    parts: IndexMap<String, PartFile>,
    done: Vec<Item>,
    /// browser downloads are numbered, so that no path is in the state
    serial: u64,
    history: IndexMap<String, VecDeque<f64>>,
    rate: f64,
    at: Option<f64>,
    patterns: Patterns,
}

impl Tracker {
    pub fn new(settings: Settings, places: Places, find_folder: FindFolder) -> Tracker {
        Tracker {
            steam_on: settings.steam,
            browser_on: settings.browser,
            min_bytes: settings.min_mb.max(0.0) * MB,
            steam_path: settings.steam_path,
            folder_given: settings.folder,
            find_folder,
            places,
            notes: Vec::new(),
            root: None,
            libraries: Vec::new(),
            folder: None,
            places_at: -PLACES_S,
            warned: false,
            log_pos: None,
            apps: IndexMap::new(),
            parts: IndexMap::new(),
            done: Vec::new(),
            serial: 0,
            history: IndexMap::new(),
            rate: 0.0,
            at: None,
            patterns: patterns(),
        }
    }

    // -- one look ----------------------------------------------------------------------------

    /// One look; an error is what Python would raise there.
    pub fn step(&mut self, now: f64, rate: Option<f64>) -> Result<Option<Dict>, String> {
        let dt = match self.at.map(|at| now - at) {
            None => 1.0,
            Some(dt) if dt < 5.0 => dt.max(0.0),
            Some(_) => 5.0,
        };
        self.at = Some(now);
        let raw = rate.map_or(0.0, |rate| if rate > 0.0 { rate } else { 0.0 });
        self.rate += (raw - self.rate) * (dt / 2.0).min(1.0); // smoothed over about two seconds
        self.look_up(now)?;
        let mut items = Vec::new();
        if self.steam_on {
            items.extend(self.steam_step(now, dt));
        }
        if self.browser_on {
            items.extend(self.browser_step(now));
        }
        self.done.retain(|item| item.until > now);
        items.extend(self.done.iter().cloned());
        for item in &items {
            if item.phase != Phase::Done {
                let history = self.history.entry(item.id.clone()).or_default();
                if history.len() == HISTORY_S {
                    history.pop_front();
                }
                history.push_back(item.rate);
            }
        }
        self.history
            .retain(|key, _| items.iter().any(|item| item.id == *key));
        let Some(mut main) = items.first() else {
            return Ok(None);
        };
        for item in &items[1..] {
            let (a, b) = (item.phase != Phase::Done, main.phase != Phase::Done);
            let more = if a != b {
                a
            } else if item.rate != main.rate {
                item.rate > main.rate
            } else {
                item.loaded > main.loaded
            };
            if more {
                main = item;
            }
        }
        self.state(main, items.len() - 1, now).map(Some)
    }

    fn state(&self, item: &Item, more: usize, now: f64) -> Result<Dict, String> {
        let history = self.history.get(&item.id);
        let history: Result<Vec<Value>, String> = (history.into_iter().flatten())
            .map(|rate| rounded(*rate))
            .collect();
        let mut state = Dict::default();
        let mut set = |key: &str, value: Value| state.insert(key.to_string(), value);
        set("id", Value::Str(item.id.clone()));
        set("source", Value::Str(item.source.into()));
        set("name", Value::Str(item.name.clone()));
        set("phase", Value::Str(item.phase.name().into()));
        set("bytes", rounded(item.loaded)?);
        set(
            "total",
            match item.total.filter(|total| *total != 0.0) {
                Some(total) => rounded(total)?,
                None => Value::None,
            },
        );
        set("rate", rounded(item.rate)?);
        set(
            "remaining_s",
            item.remaining.map_or(Value::None, |remaining| {
                Value::Float(lp_py::round_digits(remaining, 1))
            }),
        );
        set(
            "elapsed_s",
            Value::Float(lp_py::round_digits(now - item.started, 1)),
        );
        set("estimated", Value::Bool(item.estimated));
        set("history", Value::List(history?));
        set("more", Value::int(i64::try_from(more).unwrap_or(i64::MAX)));
        Ok(state)
    }

    /// Steam's libraries and the Downloads folder, looked up again now and then.
    fn look_up(&mut self, now: f64) -> Result<(), String> {
        if now - self.places_at < PLACES_S {
            return Ok(());
        }
        self.places_at = now;
        if self.steam_on {
            self.root = steam::find_root(&self.steam_path, &self.places);
            self.libraries = self.root.as_ref().map(steam::libraries).unwrap_or_default();
            if self.root.is_none() && !self.steam_path.is_empty() && !self.warned {
                self.warned = true;
                let said = format!("Steam: {} has no steamapps folder", self.steam_path);
                self.notes.push(said);
            }
        }
        if self.browser_on {
            self.folder = Some(match self.folder_given.is_empty() {
                true => (self.find_folder)(),
                false => self.given_folder()?,
            });
        }
        Ok(())
    }

    /// `Path(folder).expanduser()`.
    fn given_folder(&self) -> Result<Path, String> {
        let homes = paths::Homes {
            env: &*self.places.env,
            user: &paths::user_home,
        };
        (Path::new(HERE, &self.folder_given).expanduser(&homes))
            .ok_or_else(|| "RuntimeError: Could not determine home directory.".to_string())
    }

    // -- Steam -------------------------------------------------------------------------------

    /// The lines Steam's log gained since the last look, and whether this was the first
    /// look (what it holds then is history).
    fn tail(&mut self) -> (Vec<String>, bool) {
        let Some(root) = &self.root else {
            return (Vec::new(), false);
        };
        let path = root.join("logs").join("content_log.txt").text();
        let Ok(size) = std::fs::metadata(&path).map(|found| found.len()) else {
            return (Vec::new(), false);
        };
        let first = self.log_pos.is_none();
        let mut pos = self.log_pos.unwrap_or(size.saturating_sub(TAIL_BYTES));
        if size < pos {
            pos = 0; // a new log was begun
        }
        self.log_pos = Some(pos);
        if size == pos {
            return (Vec::new(), first);
        }
        let read = || -> std::io::Result<Vec<u8>> {
            let mut stream = std::fs::File::open(&path)?;
            stream.seek(SeekFrom::Start(pos))?;
            let mut raw = Vec::new();
            stream.take(size - pos).read_to_end(&mut raw)?;
            Ok(raw)
        };
        let Ok(raw) = read() else {
            return (Vec::new(), first); // it went away, or is locked: the next look
        };
        let Some(end) = raw.iter().rposition(|byte| *byte == b'\n') else {
            return (Vec::new(), first); // not a whole line yet
        };
        let middle = first && pos > 0; // the first line of a tail may be cut
        self.log_pos = Some(pos + end as u64 + 1);
        let text = String::from_utf8_lossy(&raw[..end]);
        let lines = lp_py::splitlines(&text);
        let lines = lines.iter().skip(usize::from(middle));
        (lines.map(|line| (*line).to_string()).collect(), first)
    }

    fn manifest(&self, appid: &str) -> Dict {
        for library in &self.libraries {
            let path = library
                .join("steamapps")
                .join(&format!("appmanifest_{appid}.acf"));
            if is_file(&path) {
                return match steam::read_vdf(&path).get("appstate") {
                    Some(Value::Dict(state)) => state.clone(),
                    _ => Dict::default(),
                };
            }
        }
        Dict::default()
    }

    fn steam_step(&mut self, now: f64, dt: f64) -> Vec<Item> {
        let (lines, first) = self.tail();
        for line in lines {
            self.steam_line(&line, now, first);
        }
        let mut items = Vec::new();
        let appids: Vec<String> = self.apps.keys().cloned().collect();
        for appid in appids {
            let manifest = self.manifest(&appid);
            let (rate_now, min_bytes) = (self.rate, self.min_bytes);
            let Some(app) = self.apps.get_mut(&appid) else {
                continue;
            };
            if let Some(name) = manifest.get("name").filter(|name| name.truthy()) {
                app.name = steam::tidy(&name.str());
            }
            let [flags, total, loaded] = MANIFEST_KEYS.map(|key| whole(manifest.get(key)));
            let zero = BigInt::default();
            // the log says "running", the manifest long "installed": what was left from before
            // the look began, or a Steam that ended
            let left_over = flags != zero
                && (&flags & BigInt::from(STEAM_RUNNING)) == zero
                && (first || now - app.log_at > STEAM_STALE_S);
            let over = match app.phase {
                Phase::Done => now >= app.done_until,
                _ => left_over,
            };
            if over {
                self.apps.shift_remove(&appid);
                continue;
            }
            if app.total == 0.0 && total != zero {
                app.total = float(&total);
            }
            if loaded != zero && loaded != app.manifest_loaded {
                app.loaded = float(&loaded);
                (app.manifest_loaded, app.manifest_at) = (loaded, now);
                app.moving_at = now; // data flows, whatever the network reading says
            }
            let downloading = app.phase == Phase::Downloading;
            if downloading {
                app.loaded += rate_now * dt;
                if rate_now >= MB / 2.0 {
                    app.moving_at = now;
                }
            }
            let known = Some(app.total).filter(|total| *total != 0.0);
            let mut have = match known {
                Some(known) if known < app.loaded => known,
                _ => app.loaded,
            };
            if let (Phase::Installing | Phase::Done, Some(known)) = (app.phase, known) {
                have = known;
            }
            let rate = if downloading { rate_now } else { 0.0 };
            let last = if app.log_at > app.moving_at {
                app.log_at
            } else {
                app.moving_at
            };
            if downloading && now - last > STEAM_QUIET_S {
                continue; // it stands still: not shown
            }
            let unknown = known.is_none()
                && downloading
                && now - app.started >= UNKNOWN_AFTER_S
                && rate >= UNKNOWN_RATE;
            let show = match app.phase {
                Phase::Done => app.shown,
                _ => known.unwrap_or(0.0) >= min_bytes || unknown,
            };
            if !show {
                continue;
            }
            app.shown = true;
            let remaining = match known {
                Some(known) if downloading && rate > REMAINING_RATE => Some((known - have) / rate),
                _ => None,
            };
            items.push(Item {
                id: format!("steam:{appid}"),
                source: "steam",
                name: app.name.clone(),
                phase: app.phase,
                loaded: have,
                total: known,
                rate,
                remaining,
                started: app.started,
                estimated: downloading && now - app.manifest_at > 10.0,
                until: app.done_until,
            });
        }
        items
    }

    fn new_app(appid: &str, now: f64) -> App {
        let text = "App {appid}";
        let name = lp_i18n::t_format(text, &[("appid", Value::Str(appid.to_string()))]);
        App {
            name: name.unwrap_or_else(|_| lp_i18n::t(text)),
            started: now,
            phase: Phase::Preparing,
            total: 0.0,
            loaded: 0.0,
            manifest_loaded: BigInt::default(),
            manifest_at: -1e9,
            log_at: now,
            moving_at: now,
            shown: false,
            done_until: 0.0,
        }
    }

    fn steam_line(&mut self, line: &str, now: f64, first: bool) {
        let chars: Vec<char> = line.chars().collect();
        let group = |found: &lp_py::regex::Found, n: usize| found.group(&chars, n);
        let number = |found: &lp_py::regex::Found, n: usize| {
            group(found, n)
                .and_then(|digits| lp_py::int_of_str(&digits))
                .unwrap_or_default()
        };
        if let Some(found) = self.patterns.start.search(&chars) {
            let appid = group(&found, 1).unwrap_or_default();
            let (loaded, total, stage) = (number(&found, 2), number(&found, 3), number(&found, 5));
            let app =
                (self.apps.entry(appid.clone())).or_insert_with(|| Self::new_app(&appid, now));
            let whole = total != BigInt::default();
            app.total = float(if whole { &total } else { &stage });
            app.loaded = if whole { float(&loaded) } else { 0.0 };
            app.log_at = now;
            return;
        }
        if let Some(found) = self.patterns.finished.search(&chars) {
            let appid = group(&found, 1).unwrap_or_default();
            if let Some(app) = self.apps.get_mut(&appid) {
                if first || !app.shown {
                    // history, or never shown: no "done" for it
                    self.apps.shift_remove(&appid);
                } else {
                    (app.phase, app.done_until, app.log_at) = (Phase::Done, now + DONE_S, now);
                }
            }
            return;
        }
        let Some(found) = self.patterns.changed.search(&chars) else {
            return;
        };
        let appid = group(&found, 1).unwrap_or_default();
        let phase = match said(&group(&found, 2).unwrap_or_default()) {
            Said::Over => {
                if (self.apps.get(&appid)).is_some_and(|app| app.phase != Phase::Done) {
                    self.apps.shift_remove(&appid);
                }
                return;
            }
            Said::Between => {
                if let Some(app) = self.apps.get_mut(&appid) {
                    app.log_at = now;
                }
                return;
            }
            Said::Phase(phase) => phase,
        };
        let app = (self.apps.entry(appid.clone())).or_insert_with(|| Self::new_app(&appid, now));
        if app.phase != Phase::Done {
            app.phase = phase;
        }
        app.log_at = now;
    }

    // -- the browser -------------------------------------------------------------------------

    /// The part files in the folder: path -> (size, name without the suffix), by their paths.
    fn part_files(&self) -> Vec<(String, u64, String)> {
        let mut found = Vec::new();
        let Some(folder) = &self.folder else {
            return found;
        };
        let folder = folder.text();
        for name in steam::names_in(&Path::new(HERE, &folder)).unwrap_or_default() {
            let lower = lp_py::lower(&name);
            let Some(suffix) = SUFFIXES.iter().find(|suffix| lower.ends_with(*suffix)) else {
                continue;
            };
            let path = paths::join(HERE, &folder, &name);
            // (the file's own size: on Windows the folder's entry has the size of an open
            // file only once it is closed)
            let Ok(file) = std::fs::metadata(&path) else {
                continue;
            };
            if !file.is_file() {
                continue;
            }
            let kept = name.chars().count().saturating_sub(suffix.len());
            let bare: String = name.chars().take(kept).collect();
            found.push((path, file.len(), if bare.is_empty() { name } else { bare }));
        }
        found.sort_by(|a, b| a.0.cmp(&b.0)); // numbered the same on every system
        found
    }

    fn browser_step(&mut self, now: f64) -> Vec<Item> {
        let seen = self.part_files();
        let paths: Vec<String> = self.parts.keys().cloned().collect();
        let mut gone = Vec::new();
        for path in paths {
            if !seen.iter().any(|(there, _, _)| *there == path) {
                gone.extend(self.parts.shift_remove(&path));
            }
        }
        for (path, size, name) in seen {
            let Some(part) = self.parts.get_mut(&path) else {
                self.serial += 1;
                let mut part = PartFile {
                    id: format!("browser:{}", self.serial),
                    name,
                    started: now,
                    size,
                    at: now,
                    moving_at: now,
                    rate: 0.0,
                    shown: false,
                };
                // Chrome renames "Unconfirmed 123.crdownload" while it loads: the same
                // download, not a new one
                if let Some(at) = gone.iter().position(|before| before.size <= size) {
                    let before = gone.remove(at);
                    (part.id, part.started) = (before.id, before.started);
                    (part.rate, part.shown) = (before.rate, before.shown);
                }
                self.parts.insert(path, part);
                continue;
            };
            let dt = now - part.at;
            if dt > 0.0 {
                let grown = (size as f64 - part.size as f64) / dt;
                let grown = if grown > 0.0 { grown } else { 0.0 };
                part.rate += (grown - part.rate) * (dt / 2.0).min(1.0);
                if size != part.size {
                    part.moving_at = now;
                }
                (part.size, part.at) = (size, now);
            }
        }
        for part in gone {
            // finished, if the file it was becoming is there now
            let Some(folder) = self.folder.as_ref().filter(|_| part.shown) else {
                continue;
            };
            let target = folder.join(&part.name).text();
            let Ok(found) = std::fs::metadata(&target) else {
                continue;
            };
            if found.is_file() {
                let size = found.len() as f64;
                self.done.push(Item {
                    id: part.id,
                    source: "browser",
                    name: part.name,
                    phase: Phase::Done,
                    loaded: size,
                    total: Some(size),
                    rate: 0.0,
                    remaining: None,
                    started: part.started,
                    estimated: false,
                    until: now + DONE_S,
                });
            }
        }
        let mut items = Vec::new();
        for part in self.parts.values_mut() {
            let paused = now - part.moving_at > PAUSED_AFTER_S;
            let rate = if paused { 0.0 } else { part.rate };
            let counts = part.shown
                || part.size as f64 >= self.min_bytes
                || (now - part.started >= UNKNOWN_AFTER_S && rate >= UNKNOWN_RATE);
            if !counts {
                continue;
            }
            part.shown = true;
            items.push(Item {
                id: part.id.clone(),
                source: "browser",
                name: part.name.clone(),
                phase: if paused {
                    Phase::Paused
                } else {
                    Phase::Downloading
                },
                loaded: part.size as f64,
                total: None,
                rate,
                remaining: None,
                started: part.started,
                estimated: false,
                until: 0.0,
            });
        }
        items
    }
}

// -- the service --------------------------------------------------------------------------------

struct Held {
    tracker: Tracker,
    state: Option<Dict>,
    failure: Option<String>,
}

struct Shared {
    held: Mutex<Held>,
    host: ServiceHost,
    began: Instant,
    ended: AtomicBool,
    wake: (Mutex<()>, Condvar),
}

impl Shared {
    /// The network's download in bytes per second, as the panel last read it.
    fn rate(&self) -> Option<f64> {
        let snapshot = self.host.snapshot()?;
        match &snapshot.readings.get("net.down")?.value {
            Value::Float(rate) => Some(*rate),
            Value::Bool(on) => Some(f64::from(u8::from(*on))),
            value @ Value::Int(_) => value.to_f64(),
            _ => None,
        }
    }

    /// Looks once and publishes what is found. A failed look is said once, then it is tried
    /// again; it never reaches the panel.
    fn step(&self) {
        let rate = self.rate();
        let (state, notes) = {
            let Ok(mut held) = self.held.lock() else {
                return;
            };
            let now = self.began.elapsed().as_secs_f64();
            let (state, failure) = match held.tracker.step(now, rate) {
                Ok(state) => (state, None),
                Err(failure) => (None, Some(failure)),
            };
            let mut notes = std::mem::take(&mut held.tracker.notes);
            if failure != held.failure {
                notes.push(match &failure {
                    Some(failure) => format!("looking at the downloads failed: {failure}"),
                    None => "looking at the downloads works again".into(),
                });
                held.failure = failure;
            }
            // (a look that was under way when the service stopped publishes nothing)
            let quiet =
                self.ended.load(Ordering::Relaxed) || (state.is_none() && held.state.is_none());
            if !quiet {
                held.state.clone_from(&state);
            }
            ((!quiet).then_some(state), notes)
        };
        for note in notes {
            self.host.note(&note);
        }
        if let Some(state) = state {
            let value = state.map_or(Value::None, Value::Dict);
            let _ = self.host.publish_state(KEY, &value, "");
        }
    }

    fn run(&self) {
        let ended = |_: &mut ()| !self.ended.load(Ordering::Relaxed);
        loop {
            if let Ok(asleep) = self.wake.0.lock() {
                let wait = Duration::from_secs_f64(STEP_S);
                let _woken = (self.wake.1).wait_timeout_while(asleep, wait, ended);
            }
            if self.ended.load(Ordering::Relaxed) {
                return;
            }
            self.step();
        }
    }
}

/// Shows what is downloading: the state `downloads` (see docs/PLUGINS.md) is the one that
/// matters most of Steam's and the browser's downloads, or none.
pub struct Downloads {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    vec![
        ("steam", "bool", Value::Bool(true)),
        ("steam_path", "string", Value::Str(String::new())),
        ("browser", "bool", Value::Bool(true)),
        ("folder", "string", Value::Str(String::new())),
        ("min_mb", "number", Value::Float(50.0)),
    ]
}

/// The tracker's settings of a service's options.
pub fn settings_of(options: &Dict) -> Settings {
    let on = |key: &str| options.get(key).is_none_or(Value::truthy);
    let text = |key: &str| {
        let text = options.get(key).map(Value::str).unwrap_or_default();
        lp_py::strip(&text).to_string()
    };
    Settings {
        steam: on("steam"),
        browser: on("browser"),
        min_mb: options
            .get("min_mb")
            .and_then(Value::to_f64)
            .unwrap_or(50.0),
        steam_path: text("steam_path"),
        folder: text("folder"),
    }
}

/// The downloads as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let find = || {
                let places = Places::here();
                downloads_folder(system_folder(Known::Downloads), &home(&places))
            };
            let tracker = Tracker::new(settings_of(&options), Places::here(), Box::new(find));
            Ok(Box::new(Downloads {
                shared: Arc::new(Shared {
                    held: Mutex::new(Held {
                        tracker,
                        state: None,
                        failure: None,
                    }),
                    host,
                    began: Instant::now(),
                    ended: AtomicBool::new(false),
                    wake: (Mutex::new(()), Condvar::new()),
                }),
                thread: None,
            }))
        },
    }
}

impl Service for Downloads {
    fn start(&mut self) -> Result<(), String> {
        let shared = Arc::clone(&self.shared);
        let thread = std::thread::Builder::new().name("downloads".into());
        self.thread = Some(
            thread
                .spawn(move || shared.run())
                .map_err(|why| format!("the downloads' thread did not start: {why}"))?,
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

    /// What it looks at, for a console page.
    fn summary(&self) -> Option<Value> {
        let held = self.shared.held.lock().ok()?;
        let place = |path: &Option<Path>| {
            (path.as_ref()).map_or(Value::None, |path| Value::Str(path.text()))
        };
        let mut said = Dict::default();
        said.insert("steam".into(), place(&held.tracker.root));
        said.insert("folder".into(), place(&held.tracker.folder));
        let name = held.state.as_ref().and_then(|state| state.get("name"));
        said.insert("download".into(), name.cloned().unwrap_or(Value::None));
        let error = held.failure.clone().map_or(Value::None, Value::Str);
        said.insert("error".into(), error);
        Some(Value::Dict(said))
    }
}

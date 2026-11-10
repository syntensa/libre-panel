//! Frames per second of the game you play on Linux, from MangoHud's log
//! (sensors/mangohud.py).
//!
//! MangoHud (MIT license) is the overlay many Linux games run with (`mangohud %command%` in
//! Steam). It writes what it measures to a CSV file while it logs; this source reads the
//! newest of those files as it grows. MangoHud logs when its logging key is pressed
//! (Shift+F2), or from the start with `autostart_log=1` in ~/.config/MangoHud/MangoHud.conf.
//!
//! The folder is the one MangoHud.conf names (`output_folder`), else the home folder; or it
//! is named in `[sensors.mangohud]` (`folder`).
//!
//! Readings, as PresentMon's on Windows: `game.fps` (the last second), `game.frametime`
//! (ms), `game.low` (the 1% lows of the last 30 s), `game.frametime_peak` (the longest frame
//! time since the last reading), `game.app` (the program, from the file's name) and what the
//! round's frames come to. MangoHud logs a frame every `log_interval` milliseconds, so the
//! lows are those of its samples.
//!
//! The file is read only while a theme or a service shows `game.*` readings. Nothing runs
//! beside the game and nothing touches it: only the file MangoHud writes is read.

use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime};

use lp_py::regex::Regex;

use crate::frames::FrameCounter;
use crate::lhm::Readings;
use crate::service::{wanted, Wanted};

pub const POLL_SECONDS: f64 = 0.25;
/// a log not written for this long is of a game that ended
pub const FRESH_SECONDS: f64 = 10.0;
/// how often the folder is looked at for a newer log
pub const LOOK_SECONDS: f64 = 2.0;
/// nobody looked for this long: the thread ends, and starts on demand
pub const IDLE_SECONDS: f64 = 60.0;
const MAX_LINE: usize = 4096;
/// "Cyberpunk2077_2026-10-21_19-04-33.csv"; its summary ("..._summary.csv") is no log
const LOG_NAME: &str = r"^(.+?)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.csv$";

/// The program a log is of, if its name is a log's.
fn log_app(name: &str) -> Option<String> {
    static PATTERN: OnceLock<Option<Regex>> = OnceLock::new();
    let pattern = PATTERN.get_or_init(|| Regex::new(LOG_NAME, false).ok());
    let chars: Vec<char> = name.chars().collect();
    pattern.as_ref()?.matches(&chars)?.group(&chars, 1)
}

/// os.path.expandvars: `$NAME` and `${NAME}` replaced by what `env` has for them; those it
/// has not are left as they are.
pub fn expandvars(text: &str, env: &dyn Fn(&str) -> Option<String>) -> String {
    let chars: Vec<char> = text.chars().collect();
    let word = |ch: char| ch.is_ascii_alphanumeric() || ch == '_';
    let mut out = String::new();
    let mut at = 0;
    while at < chars.len() {
        if chars[at] == '$' {
            let (name, end) = if chars.get(at + 1) == Some(&'{') {
                match chars[at + 2..].iter().position(|ch| *ch == '}') {
                    Some(length) => (Some((at + 2, at + 2 + length)), at + 3 + length),
                    None => (None, at + 1),
                }
            } else {
                let length = chars[at + 1..].iter().take_while(|ch| word(**ch)).count();
                match length {
                    0 => (None, at + 1),
                    length => (Some((at + 1, at + 1 + length)), at + 1 + length),
                }
            };
            if let Some((from, to)) = name {
                let name: String = chars[from..to].iter().collect();
                match env(&name) {
                    Some(value) => out.push_str(&value),
                    None => out.extend(&chars[at..end]),
                }
                at = end;
                continue;
            }
        }
        out.push(chars[at]);
        at += 1;
    }
    out
}

/// The folder MangoHud.conf names (`output_folder`), else the home folder. `env` is the
/// environment (XDG_CONFIG_HOME, HOME and what the folder's name uses).
pub fn config_folder(home: &Path, env: &dyn Fn(&str) -> Option<String>) -> PathBuf {
    let base = match env("XDG_CONFIG_HOME").filter(|folder| !folder.is_empty()) {
        Some(folder) => PathBuf::from(folder),
        None => home.join(".config"),
    };
    let data = std::fs::read(base.join("MangoHud").join("MangoHud.conf")).unwrap_or_default();
    let text = String::from_utf8_lossy(&data);
    for line in lp_py::splitlines(&text) {
        let (key, value) = line.split_once('=').unwrap_or((line, ""));
        let value = lp_py::strip(value);
        if lp_py::strip(key) == "output_folder" && !value.is_empty() {
            let folder = expandvars(value, env);
            // (expanduser: the user's own home; another user's is left as it is written,
            // where Python looks the user up and fails if there is none)
            let own = env("HOME").map_or(home.to_path_buf(), PathBuf::from);
            return match folder.strip_prefix('~') {
                Some("") => own,
                Some(rest) if rest.starts_with('/') => own.join(rest.trim_start_matches('/')),
                _ => PathBuf::from(folder),
            };
        }
    }
    home.to_path_buf()
}

/// The log MangoHud writes now: the newest one written in the last few seconds before `now`
/// (a Unix time).
pub fn newest_log(folder: &Path, now: f64) -> Option<PathBuf> {
    let mut best: Option<(f64, PathBuf)> = None;
    for entry in std::fs::read_dir(folder).ok()?.flatten() {
        if log_app(&entry.file_name().to_string_lossy()).is_none() {
            continue;
        }
        let changed = entry.metadata().and_then(|data| data.modified());
        let Ok(changed) = changed else {
            continue;
        };
        let changed = match changed.duration_since(SystemTime::UNIX_EPOCH) {
            Ok(since) => since.as_secs_f64(),
            Err(before) => -before.duration().as_secs_f64(),
        };
        if now - changed <= FRESH_SECONDS && best.as_ref().is_none_or(|(at, _)| changed > *at) {
            best = Some((changed, entry.path()));
        }
    }
    best.map(|(_, path)| path)
}

/// The program of a log, from its name.
pub fn app_of(path: &Path) -> String {
    let name = path.file_name().map(|name| name.to_string_lossy());
    let name = name.unwrap_or_default();
    log_app(&name).unwrap_or_else(|| {
        let stem = path.file_stem().map(|stem| stem.to_string_lossy());
        stem.unwrap_or_default().into_owned()
    })
}

/// The rows of one log as they are written: the header line that names `frametime` first
/// (MangoHud puts what it knows of the system above it), then a frame per row.
pub struct LogReader {
    pub path: PathBuf,
    pub app: String,
    /// how far the file is read
    at: u64,
    /// a line not ended yet
    rest: Vec<u8>,
    /// where the frame time is in a row
    column: Option<usize>,
}

impl LogReader {
    pub fn new(path: &Path) -> LogReader {
        LogReader {
            path: path.to_path_buf(),
            app: app_of(path),
            at: 0,
            rest: Vec::new(),
            column: None,
        }
    }

    /// The frame times (ms) of the rows written since the last call.
    pub fn frames(&mut self) -> Vec<f64> {
        let mut data = Vec::new();
        let read = std::fs::File::open(&self.path).and_then(|mut file| {
            file.seek(SeekFrom::Start(self.at))?;
            file.take(1024 * 1024).read_to_end(&mut data)
        });
        if read.is_err() {
            return Vec::new();
        }
        self.at += data.len() as u64;
        let mut whole = std::mem::take(&mut self.rest);
        whole.extend(data);
        let mut lines: Vec<&[u8]> = whole.split(|byte| *byte == b'\n').collect();
        let last = lines.pop().unwrap_or_default();
        self.rest = last[last.len().saturating_sub(MAX_LINE)..].to_vec();
        let mut found = Vec::new();
        for raw in lines {
            let line = String::from_utf8_lossy(raw);
            let row: Vec<&str> = lp_py::strip(&line).split(',').collect();
            let Some(column) = self.column else {
                self.column = row
                    .iter()
                    .position(|name| lp_py::lower(lp_py::strip(name)) == "frametime");
                continue;
            };
            if let Some(ms) = row.get(column).and_then(|cell| lp_py::float_of_str(cell)) {
                found.push(ms);
            }
        }
        found
    }
}

struct Shared {
    counter: Mutex<FrameCounter>,
    /// (stop, the thread runs)
    flags: Mutex<(bool, bool)>,
    changed: Condvar,
    notes: Mutex<Vec<String>>,
    wanted: Wanted,
    /// the folder `[sensors.mangohud]` names
    folder: Option<PathBuf>,
    began: Instant,
}

impl Shared {
    fn folder(&self) -> PathBuf {
        if let Some(folder) = &self.folder {
            return folder.clone();
        }
        let env = |name: &str| std::env::var(name).ok();
        let home = env("HOME").map_or(PathBuf::from("/"), PathBuf::from);
        config_folder(&home, &env)
    }
}

fn run(shared: &Shared) {
    let mut reader: Option<LogReader> = None;
    let mut looked = Instant::now();
    let mut checked: Option<Instant> = None;
    loop {
        if shared.flags.lock().map(|flags| flags.0).unwrap_or(true) {
            break;
        }
        let now = Instant::now();
        if wanted(&shared.wanted.keys(), "game.") {
            looked = now;
        } else if now.duration_since(looked).as_secs_f64() > IDLE_SECONDS {
            break;
        }
        if checked.is_none_or(|at| now.duration_since(at).as_secs_f64() >= LOOK_SECONDS) {
            checked = Some(now);
            let clock = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH);
            let path = newest_log(
                &shared.folder(),
                clock.map_or(0.0, |since| since.as_secs_f64()),
            );
            match path {
                None => reader = None,
                Some(path) if reader.as_ref().is_none_or(|reader| reader.path != path) => {
                    if let Ok(mut notes) = shared.notes.lock() {
                        notes.push(format!("mangohud: reading the log of {}", app_of(&path)));
                    }
                    let mut new = LogReader::new(&path);
                    new.frames(); // what was logged before is not now
                    reader = Some(new);
                }
                Some(_) => {}
            }
        }
        if let Some(reader) = &mut reader {
            let frames = reader.frames();
            let at = now.duration_since(shared.began).as_secs_f64();
            if let Ok(mut counter) = shared.counter.lock() {
                for ms in frames {
                    counter.add(&reader.app, ms, at, None);
                }
            }
        }
        let Ok(flags) = shared.flags.lock() else {
            break;
        };
        let wait = Duration::from_secs_f64(POLL_SECONDS);
        if shared
            .changed
            .wait_timeout_while(flags, wait, |flags| !flags.0)
            .map(|(flags, _)| flags.0)
            .unwrap_or(true)
        {
            break;
        }
    }
    if let Ok(mut flags) = shared.flags.lock() {
        flags.1 = false;
    }
}

/// `game.*` from MangoHud's log, while a theme or a service shows them.
pub struct MangoHudSource {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
}

impl MangoHudSource {
    /// `folder`: `[sensors.mangohud] folder`, "" for the one MangoHud.conf names.
    pub fn new(folder: &str, wanted: Wanted) -> MangoHudSource {
        let folder = match folder {
            "" => None,
            "~" => std::env::var("HOME").ok().map(PathBuf::from),
            folder => Some(match (folder.strip_prefix("~/"), std::env::var("HOME")) {
                (Some(rest), Ok(home)) => Path::new(&home).join(rest),
                _ => PathBuf::from(folder),
            }),
        };
        MangoHudSource {
            shared: Arc::new(Shared {
                counter: Mutex::new(FrameCounter::new(false)), // (a frame every log_interval)
                flags: Mutex::new((false, false)),
                changed: Condvar::new(),
                notes: Mutex::new(Vec::new()),
                wanted,
                folder,
                began: Instant::now(),
            }),
            thread: None,
        }
    }

    pub fn read(&mut self) -> Readings {
        let (stopped, runs) = self
            .shared
            .flags
            .lock()
            .map(|f| *f)
            .unwrap_or((true, false));
        if !runs {
            if stopped || !wanted(&self.shared.wanted.keys(), "game.") {
                return Readings::new();
            }
            if let Some(ended) = self.thread.take() {
                let _ = ended.join();
            }
            if let Ok(mut flags) = self.shared.flags.lock() {
                flags.1 = true;
            }
            let shared = self.shared.clone();
            self.thread = std::thread::Builder::new()
                .name("sensors-mangohud".into())
                .spawn(move || run(&shared))
                .ok();
        }
        let now = self.shared.began.elapsed().as_secs_f64();
        let counter = self.shared.counter.lock();
        counter
            .map(|mut counter| counter.readings(now))
            .unwrap_or_default()
    }

    /// What the source noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        let notes = self.shared.notes.lock();
        notes
            .map(|mut notes| std::mem::take(&mut *notes))
            .unwrap_or_default()
    }

    pub fn close(&mut self) {
        if let Ok(mut flags) = self.shared.flags.lock() {
            flags.0 = true;
        }
        self.shared.changed.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for MangoHudSource {
    fn drop(&mut self) {
        self.close();
    }
}

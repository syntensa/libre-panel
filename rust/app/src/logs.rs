//! The app's log (cli.py `_log_file`, logview.py): `libre-panel.log` in the settings folder,
//! a line per entry ("date time,ms LEVEL logger: message"), at most three files of a megabyte
//! (logging's RotatingFileHandler); read back for Status → Diagnose: its last entries, the
//! whole of it to save, and the detail it is written with, set without a restart. What leaves
//! through here has the person's own folders taken out, as the doctor's report does.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use lp_py::{Dict, Value};

pub const LEVELS: [&str; 5] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
const MAX_ENTRIES: usize = 500;
const MAX_BYTES: usize = 3 << 20; // all three files at most
const HANG_BYTES: usize = 256 << 10; // of the hangs, the last
const HANG_FILENAME: &str = "libre-panel-hang.log";
/// RotatingFileHandler(maxBytes=1_000_000, backupCount=2)
const FILE_BYTES: u64 = 1_000_000;
const BACKUPS: u32 = 2;

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Debug = 10,
    Info = 20,
    Warning = 30,
}

impl Level {
    fn name(self) -> &'static str {
        match self {
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warning => "WARNING",
        }
    }
}

/// the root logger at DEBUG (`-v`, or "Detailed log" on), else at INFO
static VERBOSE: AtomicBool = AtomicBool::new(false);

struct Sink {
    path: PathBuf,
    file: File,
    size: u64,
}

static SINK: Mutex<Option<Sink>> = Mutex::new(None);

/// _log_file: the background app's entries into the log file from now on (there is no
/// terminal to read); its path.
pub fn to_file() -> std::io::Result<PathBuf> {
    let path = lp_config::dirs::log_file();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = OpenOptions::new().create(true).append(true).open(&path)?;
    let size = file.metadata().map_or(0, |m| m.len());
    let mut sink = SINK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *sink = Some(Sink {
        path: path.clone(),
        file,
        size,
    });
    Ok(path)
}

/// logging.Formatter's asctime: the local time, and its milliseconds.
pub(crate) fn asctime() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as f64;
    let local = lp_py::zones::local();
    let moment = local.and_then(|zone| lp_py::zones::fromtimestamp(zone, secs));
    let millis = now.subsec_millis();
    match moment {
        Some((at, _)) => format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02},{millis:03}",
            at.year, at.month, at.day, at.hour, at.minute, at.second
        ),
        None => format!("{secs},{millis:03}"),
    }
}

/// RotatingFileHandler.doRollover: the log becomes `.1`, `.1` becomes `.2`.
fn roll_over(sink: &mut Sink) -> std::io::Result<()> {
    let numbered = |n: u32| {
        let name = sink.path.file_name().unwrap_or_default().to_string_lossy();
        sink.path.with_file_name(format!("{name}.{n}"))
    };
    for n in (1..BACKUPS).rev() {
        let (from, to) = (numbered(n), numbered(n + 1));
        if from.exists() {
            if to.exists() {
                std::fs::remove_file(&to)?;
            }
            std::fs::rename(&from, &to)?;
        }
    }
    let first = numbered(1);
    if first.exists() {
        std::fs::remove_file(&first)?;
    }
    if sink.path.exists() {
        std::fs::rename(&sink.path, &first)?;
    }
    sink.file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&sink.path)?;
    sink.size = 0;
    Ok(())
}

/// `panel 2: ` before what the loop of a panel beside the first says (cli._PanelOfThread).
fn panel_of_thread() -> String {
    match std::thread::current().name() {
        Some(name) if name.starts_with("panel-") => format!("panel {}: ", &name[6..]),
        _ => String::new(),
    }
}

/// An entry of `logger` at `level`, into the log file where the app keeps one; nothing below
/// the root's level.
pub fn log(level: Level, logger: &str, message: &str) {
    let least = if VERBOSE.load(Ordering::Relaxed) {
        Level::Debug
    } else {
        Level::Info
    };
    if level < least {
        return;
    }
    let mut guard = SINK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some(sink) = guard.as_mut() else {
        return;
    };
    let line = format!(
        "{} {} {logger}: {}{message}\n",
        asctime(),
        level.name(),
        panel_of_thread()
    );
    // (shouldRollover: the file's end and the entry's characters against maxBytes)
    if sink.size + line.chars().count() as u64 >= FILE_BYTES && sink.path.is_file() {
        let _ = roll_over(sink);
    }
    // (a text file of Python's: "\r\n" for each line's end on Windows)
    let written = match cfg!(windows) {
        true => line.replace('\n', "\r\n"),
        false => line,
    };
    if sink.file.write_all(written.as_bytes()).is_ok() {
        sink.size += written.len() as u64;
    }
}

/// What the app says of itself: on the standard error as it was, and into the log.
pub fn say(logger: &str, message: &str) {
    eprintln!("libre-panel: {}{message}", panel_of_thread());
    log(Level::Info, logger, message);
}

pub fn verbose() -> bool {
    VERBOSE.load(Ordering::Relaxed)
}

/// set_verbose: every detail into the log from now on (as `-v`), or only what matters; until
/// the app starts again.
pub fn set_verbose(on: bool) {
    VERBOSE.store(on, Ordering::Relaxed);
    let said = if on { "on" } else { "off" };
    log(
        Level::Info,
        "libre_panel.logview",
        &format!("detailed log {said}"),
    );
}

// -- reading it back --------------------------------------------------------------------------

/// The log and its older parts, the oldest first.
fn files(path: &Path) -> Vec<PathBuf> {
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let older = [2, 1].map(|n| path.with_file_name(format!("{name}.{n}")));
    older
        .into_iter()
        .chain([path.to_path_buf()])
        .filter(|p| p.is_file())
        .collect()
}

/// _read: the newest files first, until the budget is spent; a file cut from a whole line.
fn read(path: &Path, budget: usize) -> String {
    let mut budget = budget as i64;
    let mut parts: Vec<String> = Vec::new();
    for file in files(path).iter().rev() {
        let Ok(mut data) = std::fs::read(file) else {
            continue;
        };
        if data.len() as i64 > budget {
            let keep = usize::try_from(budget).unwrap_or(0);
            data = data[data.len() - keep..].to_vec();
            let cut = data.iter().position(|&b| b == b'\n').map_or(0, |i| i + 1);
            data = data[cut..].to_vec();
        }
        budget -= data.len() as i64;
        parts.insert(0, String::from_utf8_lossy(&data).into_owned());
        if budget <= 0 {
            break;
        }
    }
    parts.concat()
}

/// re.sub(re.escape(spelt), "~", text, flags=re.IGNORECASE)
fn replaced_ignoring_case(text: &[char], spelt: &[char]) -> Vec<char> {
    let mut out = Vec::with_capacity(text.len());
    let mut i = 0;
    while i < text.len() {
        let here = text.len() - i >= spelt.len()
            && spelt
                .iter()
                .zip(&text[i..])
                .all(|(&p, &c)| lp_py::re::same_ignoring_case(p, c));
        if here && !spelt.is_empty() {
            out.push('~');
            i += spelt.len();
        } else {
            out.push(text[i]);
            i += 1;
        }
    }
    out
}

/// doctor._FOLDERS at `k` (after any `file:/*`): a drive's root not after a letter, or `\\`;
/// where it ends.
fn root_at(text: &[char], k: usize) -> Option<usize> {
    let letter = |c: char| c.is_ascii_alphabetic();
    let after_letter = k > 0 && letter(text[k - 1]);
    if !after_letter
        && text.get(k).is_some_and(|&c| letter(c))
        && text.get(k + 1) == Some(&':')
        && text.get(k + 2).is_some_and(|&c| c == '\\' || c == '/')
    {
        return Some(k + 3);
    }
    (text.get(k) == Some(&'\\') && text.get(k + 1) == Some(&'\\')).then_some(k + 2)
}

/// doctor._FOLDERS: a path's folders, from where it starts.
fn folders_at(text: &[char], i: usize) -> Option<usize> {
    let prefix = "file:".chars().collect::<Vec<_>>();
    let mut end = None;
    if text.len() >= i + prefix.len() && text[i..i + prefix.len()] == prefix[..] {
        let mut j = i + prefix.len();
        while text.get(j) == Some(&'/') {
            j += 1;
        }
        end = root_at(text, j);
    }
    let mut end = end.or_else(|| root_at(text, i))?;
    let inside = |c: char| !"\\/:*?\"<>|;\r\n".contains(c);
    loop {
        let mut j = end;
        while text.get(j).is_some_and(|&c| inside(c)) {
            j += 1;
        }
        match text.get(j) {
            Some('\\' | '/') if j > end => end = j + 1,
            _ => return Some(end),
        }
    }
}

/// doctor.without_folders: the home folder becomes `~`, the folders of any other path `…\`;
/// the file a path names stays.
pub fn without_folders(text: &str, home: Option<&str>) -> String {
    let home = match home {
        Some(home) => home.to_string(),
        None => {
            let found = crate::autostart::user_home();
            match found.as_os_str().is_empty() {
                true => "~".to_string(),
                false => found.to_string_lossy().into_owned(),
            }
        }
    };
    let mut chars: Vec<char> = text.chars().collect();
    if home.chars().count() > 3 {
        let mut spellings = vec![home.clone()];
        let forward = home.replace('\\', "/");
        if forward != home {
            spellings.push(forward);
        }
        for spelt in spellings {
            let spelt: Vec<char> = spelt.chars().collect();
            chars = replaced_ignoring_case(&chars, &spelt);
        }
    }
    let mut out = String::with_capacity(chars.len());
    let mut i = 0;
    while i < chars.len() {
        match folders_at(&chars, i) {
            Some(end) => {
                out.push_str("…\\");
                i = end;
            }
            None => {
                out.push(chars[i]);
                i += 1;
            }
        }
    }
    out
}

/// logview._ENTRY: (time, level, logger, message) of an entry's first line.
fn entry(line: &str) -> Option<(String, String, String, String)> {
    let chars: Vec<char> = line.chars().collect();
    let digits = |from: usize, n: usize| {
        (from..from + n).all(|i| chars.get(i).is_some_and(char::is_ascii_digit))
    };
    let at = |i: usize, c: char| chars.get(i) == Some(&c);
    if !(digits(0, 4)
        && at(4, '-')
        && digits(5, 2)
        && at(7, '-')
        && digits(8, 2)
        && at(10, ' ')
        && digits(11, 2)
        && at(13, ':')
        && digits(14, 2)
        && at(16, ':')
        && digits(17, 2))
    {
        return None;
    }
    let time: String = chars[..19].iter().collect();
    let mut i = 19;
    if at(i, ',') && digits(i + 1, 1) {
        i += 1;
        while chars.get(i).is_some_and(char::is_ascii_digit) {
            i += 1;
        }
    }
    if !at(i, ' ') {
        return None;
    }
    i += 1;
    let rest: String = chars[i..].iter().collect();
    let level = LEVELS
        .iter()
        .find(|level| rest.starts_with(&format!("{level} ")))?;
    let rest = &rest[level.len() + 1..];
    let name_end = rest
        .char_indices()
        .find(|&(_, c)| !(c.is_alphanumeric() || c == '_' || c == '.' || c == '-'))
        .map_or(rest.len(), |(i, _)| i);
    if name_end == 0 || !rest[name_end..].starts_with(':') {
        return None;
    }
    let logger = &rest[..name_end];
    let message = rest[name_end + 1..]
        .strip_prefix(' ')
        .unwrap_or(&rest[name_end + 1..]);
    Some((
        time,
        level.to_string(),
        logger.to_string(),
        message.to_string(),
    ))
}

fn rank(level: &str) -> usize {
    LEVELS.iter().position(|l| *l == level).unwrap_or(0)
}

/// entries: the last entries of `level` and worse, the oldest first: `{time, level, logger,
/// message}`, a traceback's lines in its message.
pub fn entries(level: &str, path: &Path, home: Option<&str>) -> Vec<Value> {
    let least = rank(level);
    let text = without_folders(&read(path, 512 << 10), home);
    let mut found: Vec<(String, String, String, String)> = Vec::new();
    for line in lp_py::splitlines(&text) {
        if let Some(e) = entry(line) {
            found.push(e);
        } else if !line.trim().is_empty() {
            if let Some(last) = found.last_mut() {
                last.3.push('\n');
                last.3.push_str(line);
            }
        }
    }
    let kept: Vec<_> = found.into_iter().filter(|e| rank(&e.1) >= least).collect();
    let skip = kept.len().saturating_sub(MAX_ENTRIES);
    kept.into_iter()
        .skip(skip)
        .map(|(time, level, logger, message)| {
            let mut d = Dict::new();
            d.insert("time".into(), Value::Str(time));
            d.insert("level".into(), Value::Str(level));
            d.insert("logger".into(), Value::Str(logger));
            d.insert("message".into(), Value::Str(message));
            Value::Dict(d)
        })
        .collect()
}

/// text: the whole log to save and send along, without the person's folders; after it, where
/// the panel loop stood when it hung, if it ever did.
pub fn text(path: &Path, home: Option<&str>) -> String {
    let mut whole = read(path, MAX_BYTES);
    let hang = path.with_file_name(HANG_FILENAME);
    let hangs = std::fs::read(&hang).map_or(String::new(), |data| {
        let from = data.len().saturating_sub(HANG_BYTES);
        String::from_utf8_lossy(&data[from..]).into_owned()
    });
    if hangs.contains("Timeout") {
        whole.push_str(&format!("\n== {HANG_FILENAME} ==\n{hangs}"));
    }
    without_folders(&whole, home)
}

/// `GET /api/logs?level=WARNING`: the log's last entries and whether it is written in detail.
pub fn state(level: &str) -> Value {
    let path = lp_config::dirs::log_file();
    let mut d = Dict::new();
    d.insert(
        "entries".into(),
        Value::List(entries(&level.to_uppercase(), &path, None)),
    );
    d.insert("verbose".into(), Value::Bool(verbose()));
    d.insert(
        "levels".into(),
        Value::List(LEVELS.iter().map(|&l| Value::from(l)).collect()),
    );
    Value::Dict(d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folders_go_as_the_doctor_takes_them_out() {
        let home = Some("/home/ann");
        assert_eq!(
            without_folders("at /HOME/ann/x and C:\\Users\\Bob\\f.txt", home),
            "at ~/x and …\\f.txt"
        );
        assert_eq!(
            without_folders("file:///D:/a/b/c.dll \\\\srv\\share\\f", home),
            "…\\\\srv\\share\\f" // (a space is part of a folder's name)
        );
        assert_eq!(without_folders("xC:\\a\\b", home), "xC:\\a\\b");
    }

    #[test]
    fn an_entry_and_its_traceback() {
        let dir = std::env::temp_dir().join(format!("lp-logs-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("libre-panel.log");
        std::fs::write(
            &path,
            "2026-01-02 03:04:05,678 WARNING libre_panel.x: it went\nTraceback\n  line\n\
             2026-01-02 03:04:06 INFO a:b\nnoise\n",
        )
        .unwrap();
        let all = entries("WARNING", &path, Some("/nowhere"));
        assert_eq!(all.len(), 1);
        let Value::Dict(d) = &all[0] else { panic!() };
        assert_eq!(
            d.get("message"),
            Some(&Value::from("it went\nTraceback\n  line"))
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}

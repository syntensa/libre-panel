//! Whether a newer Libre Panel is out, for Start to say so (editor/updates.py).
//!
//! Only where config.toml's `check_updates` is on: at most once a day Libre Panel asks
//! GitHub for its latest release. That request carries what any does (the computer's
//! address, "libre-panel/<version>"), nothing of the person or the panel. `updates.json`
//! beside config.toml keeps the answer and when it came, and the version whose card was
//! closed ("later"). Only releases are offered; a pre-release that runs hears of the
//! pre-releases of its own minor version too, never of a draft.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use lp_py::{Dict, Value};

const UPDATES_FILENAME: &str = "updates.json";
const LATEST_URL: &str = "https://api.github.com/repos/syntensa/libre-panel/releases/latest";
/// a pre-release that runs looks at the latest releases, pre-releases among them
const RELEASES_URL: &str = "https://api.github.com/repos/syntensa/libre-panel/releases?per_page=30";
/// where the files of the releases are: the only place an update is fetched from
pub const RELEASE_FILES: &str = "https://github.com/syntensa/libre-panel/releases/download/";
pub const CHECKSUMS: &str = "SHA256SUMS";
const DAY_S: f64 = 24.0 * 3600.0;
/// after no answer: the next try an hour later, not at each look at Start
const RETRY_S: f64 = 3600.0;
/// of the release notes, the start (their first paragraph says what is new)
const NOTES_CHARS: usize = 600;
/// the tests' own: a file whose JSON stands for GitHub's answer
pub const RELEASES_ENV: &str = "LP_RELEASES_FILE";
/// the end of each download's name on a release, by how Libre Panel came
const ASSET_ENDINGS: [(&str, &str); 4] = [
    ("setup", "-windows-x64-setup.exe"),
    ("portable", "-windows-x64-portable.zip"),
    ("appimage", "-x86_64.AppImage"),
    ("linux", "-linux-x64.tar.gz"),
];

/// A version as numbers to compare: `(major, minor, patch, fix, stage, number)`, a
/// pre-release's stage (a 0, b 1, rc 2) before the release itself (3).
pub type Key = (u32, u32, u32, u32, u32, u32);

/// version_key: `"v0.6.1"`, `"0.6"`, a hotfix's `"0.6.8.1"` or a pre-release's
/// `"0.7.0rc1"` as numbers to compare; None for what is no version.
pub fn version_key(text: &str) -> Option<Key> {
    let text = lp_py::strip(text);
    let text = text.strip_prefix('v').unwrap_or(text);
    let number = |part: &str| {
        let fits = (1..=4).contains(&part.len()) && part.bytes().all(|b| b.is_ascii_digit());
        fits.then(|| part.parse::<u32>().ok()).flatten()
    };
    // the stage at the end: a, b or rc and its number
    let digits_from = text.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    let (head, stage, stage_number) = {
        let before = &text[..digits_from];
        let tail = &text[digits_from..];
        let staged = [("rc", 2), ("a", 0), ("b", 1)]
            .into_iter()
            .find_map(|(word, stage)| before.strip_suffix(word).map(|head| (head, stage)));
        match staged {
            Some((head, stage)) if !head.is_empty() => (head, stage, Some(number(tail)?)),
            _ => (text, 3, None),
        }
    };
    let parts: Vec<&str> = head.split('.').collect();
    if !(2..=4).contains(&parts.len()) {
        return None;
    }
    let mut numbers = [0u32; 4];
    for (n, part) in parts.iter().enumerate() {
        numbers[n] = number(part)?;
    }
    Some((
        numbers[0],
        numbers[1],
        numbers[2],
        numbers[3],
        stage,
        stage_number.unwrap_or(0),
    ))
}

/// newer: whether `latest` is a later release than `current` (an unknown one is not).
pub fn newer(latest: &str, current: &str) -> bool {
    matches!((version_key(latest), version_key(current)), (Some(a), Some(b)) if a > b)
}

/// prerelease: whether `version` is a pre-release (`0.7.0rc1`).
pub fn prerelease(version: &str) -> bool {
    version_key(version).is_some_and(|key| key.4 < 3)
}

fn text_of<'a>(data: &'a Dict, key: &str) -> Option<&'a str> {
    data.get(key).and_then(Value::as_str)
}

/// _offered: whether a release of GitHub's answer is one to offer: never a draft, a
/// pre-release only to a pre-release that runs and only of its own minor version.
fn offered(data: &Value, current: &str) -> bool {
    let Value::Dict(data) = data else {
        return false;
    };
    if data.get("draft").is_some_and(Value::truthy) {
        return false;
    }
    let Some(key) = text_of(data, "tag_name").and_then(version_key) else {
        return false;
    };
    let pre = data.get("prerelease").is_some_and(Value::truthy) || key.4 < 3;
    if !pre {
        return true;
    }
    let mine = version_key(current);
    mine.is_some_and(|mine| prerelease(current) && (key.0, key.1) == (mine.0, mine.1))
}

/// _pick: the release of GitHub's answer to offer: the latest release itself, or of the
/// list a pre-release asks for the highest version that is offered.
fn pick<'a>(data: &'a Value, current: &str) -> Option<&'a Dict> {
    let chosen = match data {
        Value::List(items) => {
            let mut best: Option<(&Value, Key)> = None;
            for item in items.iter().filter(|item| offered(item, current)) {
                let Value::Dict(table) = item else { continue };
                let key = text_of(table, "tag_name").and_then(version_key)?;
                // (max keeps the first of equal ones)
                if best.is_none_or(|(_, top)| key > top) {
                    best = Some((item, key));
                }
            }
            best.map(|(item, _)| item)
        }
        other => offered(other, current).then_some(other),
    };
    match chosen {
        Some(Value::Dict(table)) => Some(table),
        _ => None,
    }
}

/// install_kind: how this Libre Panel came: "setup" (the Windows setup), "portable" (the
/// Windows .zip), "appimage", "linux" (the Linux .tar.gz) or "python" (built from its
/// source), which says the download that replaces it.
pub fn install_kind() -> &'static str {
    // (the tests' own: how Libre Panel came, instead of what it finds)
    if let Ok(kind) = std::env::var(crate::install::KIND_ENV) {
        if let Some(known) = ["setup", "portable", "appimage", "linux", "python"]
            .into_iter()
            .find(|known| *known == kind)
        {
            return known;
        }
    }
    if !crate::settings::packaged() {
        return "python";
    }
    if cfg!(windows) {
        // Inno Setup leaves its uninstaller beside the program it installed
        let program = std::env::current_exe().unwrap_or_default();
        return match program.with_file_name("unins000.exe").is_file() {
            true => "setup",
            false => "portable",
        };
    }
    if std::env::var_os("APPIMAGE").is_some_and(|v| !v.is_empty()) {
        return "appimage";
    }
    "linux"
}

/// A name of a release's file, which an update keeps as the name of its download.
fn file_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    let fits = |b: &u8| b.is_ascii_alphanumeric() || b"._-".contains(b);
    (1..=128).contains(&bytes.len()) && bytes[0].is_ascii_alphanumeric() && bytes.iter().all(fits)
}

fn dict<const N: usize>(pairs: [(&str, Value); N]) -> Dict {
    pairs
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

/// _files: of a release's files the one for this kind of install (`{"name", "url",
/// "size"}`) and the address of its SHA256SUMS (""): only files of the release itself.
fn files(data: &Dict, ending: Option<&str>) -> (Value, String) {
    let tag = text_of(data, "tag_name").map(lp_py::strip).unwrap_or("");
    let own = format!("{RELEASE_FILES}{tag}/");
    let (mut asset, mut sums) = (Value::None, String::new());
    let assets = match data.get("assets") {
        Some(Value::List(assets)) => assets.as_slice(),
        _ => &[],
    };
    for item in assets {
        let Value::Dict(item) = item else { continue };
        let (Some(name), Some(url)) =
            (text_of(item, "name"), text_of(item, "browser_download_url"))
        else {
            continue;
        };
        if !url.starts_with(&own) {
            continue;
        }
        if name == CHECKSUMS {
            sums = url.to_string();
        } else if ending.is_some_and(|ending| name.ends_with(ending)) && file_name(name) {
            let size = match item.get("size") {
                Some(Value::Int(size)) => size.clone().max(0.into()),
                _ => 0.into(),
            };
            asset = Value::Dict(dict([
                ("name", Value::Str(name.into())),
                ("url", Value::Str(url.into())),
                ("size", Value::Int(size)),
            ]));
        }
    }
    (asset, sums)
}

/// _release: what Start shows of GitHub's answer: the version, its page, the start of its
/// notes, the download for this kind of install and, for "Update to", that file of the
/// release with the address of its checksums.
fn release(data: &Value, kind: &str, current: &str) -> Option<Value> {
    let data = pick(data, current)?;
    let tag = lp_py::strip(text_of(data, "tag_name")?);
    let version = tag.strip_prefix('v').unwrap_or(tag);
    version_key(version)?;
    let page = text_of(data, "html_url").filter(|page| page.starts_with("https://github.com/"))?;
    let ending = ASSET_ENDINGS
        .iter()
        .find(|(name, _)| *name == kind)
        .map(|(_, ending)| *ending);
    let mut download = String::new();
    if let Some(Value::List(assets)) = data.get("assets") {
        for asset in assets {
            let Value::Dict(asset) = asset else { continue };
            let name = text_of(asset, "name");
            let url = text_of(asset, "browser_download_url");
            if let (Some(ending), Some(name), Some(url)) = (ending, name, url) {
                if name.ends_with(ending) && url.starts_with("https://github.com/") {
                    download = url.to_string();
                }
            }
        }
    }
    let body = text_of(data, "body").unwrap_or("");
    let first = lp_py::strip(body).split("\n\n").next().unwrap_or("");
    let notes: String = lp_py::strip(&first.replace('\n', " "))
        .chars()
        .take(NOTES_CHARS)
        .collect();
    let published = text_of(data, "published_at").unwrap_or("");
    let (asset, sums) = files(data, ending);
    Some(Value::Dict(dict([
        ("version", Value::Str(version.into())),
        ("page", Value::Str(page.into())),
        ("download", Value::Str(download)),
        ("notes", Value::Str(notes)),
        ("published", Value::Str(published.into())),
        ("asset", asset),
        ("sums", Value::Str(sums)),
    ])))
}

/// fetch_latest: GitHub's answer on the latest release; where a pre-release runs, the list
/// of the latest releases, of which pick() takes one.
fn fetch_latest() -> Result<Value, String> {
    let text = match std::env::var_os(RELEASES_ENV) {
        Some(file) => std::fs::read(file).map_err(|why| why.to_string())?,
        None => {
            let url = match prerelease(lp_py::VERSION) {
                true => RELEASES_URL,
                false => LATEST_URL,
            };
            let agent = format!("libre-panel/{}", lp_py::VERSION);
            let headers = [
                ("User-Agent", agent.as_str()),
                ("Accept", "application/vnd.github+json"),
            ];
            lp_net::get(url, &headers, 10.0, 16 << 20).map_err(|why| why.text)?
        }
    };
    let text = String::from_utf8(text).map_err(|why| why.to_string())?;
    lp_py::json::loads(&text).map_err(|why| why.to_string())
}

fn now() -> f64 {
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH);
    since.map_or(0.0, |since| since.as_secs_f64())
}

/// A number as Python's isinstance(x, int | float) takes it (True among them).
fn number(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Bool(on) => Some(f64::from(u8::from(*on))),
        Value::Int(_) | Value::Float(_) => value?.to_f64(),
        _ => None,
    }
}

pub struct Updates {
    path: PathBuf,
    /// one ask at a time: the window and a browser look alike
    lock: Mutex<()>,
}

impl Updates {
    pub fn new(config_file: &Path) -> Updates {
        let folder = config_file.parent().unwrap_or(Path::new("."));
        Updates {
            path: folder.join(UPDATES_FILENAME),
            lock: Mutex::new(()),
        }
    }

    fn read(&self) -> Dict {
        let text = std::fs::read(&self.path).ok();
        let text = text.and_then(|text| String::from_utf8(text).ok());
        match text.map(|text| lp_py::json::loads(&text)) {
            Some(Ok(Value::Dict(data))) => data,
            _ => Dict::default(),
        }
    }

    fn write(&self, data: &Dict) {
        if !self.path.parent().is_some_and(Path::is_dir) {
            return; // a first start: nothing is kept yet
        }
        let text = lp_py::json::dumps(&Value::Dict(data.clone()), &lp_py::json::Dumps::default());
        let _ = std::fs::write(&self.path, text + "\n");
    }

    fn due(data: &Dict) -> bool {
        let Some(asked) = number(data.get("asked")) else {
            return true;
        };
        let wait = match data.get("error").is_some_and(Value::truthy) {
            true => RETRY_S,
            false => DAY_S,
        };
        let since = now() - asked;
        !(0.0 <= since && since < wait)
    }

    fn ask(&self, mut data: Dict) -> Dict {
        let (latest, error) = match fetch_latest() {
            Ok(answer) => match release(&answer, install_kind(), lp_py::VERSION) {
                Some(latest) => (Some(latest), String::new()),
                None => (None, "GitHub's answer named no release".to_string()),
            },
            Err(why) => (None, why),
        };
        data.insert("asked".into(), Value::Float(now()));
        data.insert("error".into(), Value::Str(error));
        if let Some(latest) = latest {
            data.insert("latest".into(), latest);
        }
        self.write(&data);
        data
    }

    /// `{"check", "current", "kind", "latest", "newer", "asked", "error", "later"}`. GitHub is
    /// asked only where `check` is on, and only once a day (`at_once`: at once, as "Look
    /// now" in Settings asks).
    pub fn state(&self, check: Option<bool>, at_once: bool) -> Value {
        let data = {
            let _asking = self.lock.lock();
            let data = self.read();
            match check == Some(true) && (at_once || Self::due(&data)) {
                true => self.ask(data),
                false => data,
            }
        };
        let on = check == Some(true);
        let latest = match data.get("latest") {
            Some(latest @ Value::Dict(_)) => Some(latest.clone()),
            _ => None,
        };
        let later = text_of(&data, "later").unwrap_or("");
        let version = match &latest {
            Some(Value::Dict(latest)) => latest.get("version").map(lp_py::format::str_of),
            _ => None,
        };
        let is_newer = on && version.is_some_and(|v| newer(&v, lp_py::VERSION));
        let asked = match data.get("asked") {
            Some(asked @ (Value::Bool(_) | Value::Int(_) | Value::Float(_))) => asked.clone(),
            _ => Value::None,
        };
        let error = match (on, data.get("error")) {
            (true, Some(Value::Str(error))) => error.clone(),
            _ => String::new(),
        };
        Value::Dict(dict([
            ("check", check.map_or(Value::None, Value::Bool)),
            ("current", Value::Str(lp_py::VERSION.into())),
            ("kind", Value::Str(install_kind().into())),
            (
                "latest",
                if on {
                    latest.unwrap_or(Value::None)
                } else {
                    Value::None
                },
            ),
            ("newer", Value::Bool(is_newer)),
            ("asked", asked),
            ("error", Value::Str(error)),
            ("later", Value::Str(later.into())),
        ]))
    }

    /// The card of `version` was closed: Start shows it no more (a later one it does).
    pub fn later(&self, version: &str, check: Option<bool>) -> Result<Value, String> {
        if version_key(version).is_none() {
            return Err("later is a version such as 0.7.0".into());
        }
        {
            let _asking = self.lock.lock();
            let mut data = self.read();
            data.insert("later".into(), Value::Str(version.into()));
            self.write(&data);
        }
        Ok(self.state(check, false))
    }
}

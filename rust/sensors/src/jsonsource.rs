//! Values from JSON: the address of a NAS, a router, a Proxmox host, a second PC, or a file
//! another program writes (sensors/json_source.py). No account: what the address answers is
//! read as it is.
//!
//! Readings: `json.<name>.<field>`, a number or a text (true and false as 1 and 0). A field is
//! a path into the answer: names and list places, `a.b[0].c` (or `a.b.0.c`). Without `fields`
//! every value up to three levels deep is read (at most 100), its key the path with dots. An
//! answer that does not come keeps the last values for three times `update_s`, then they go.
//! Each source is asked in a thread of its own, never on the frame's time.

use std::io::Read;
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_py::{Dict, Value};
use lp_render::Reading;

use crate::lhm::Readings;

pub const MAX_BYTES: usize = 1024 * 1024;
pub const MAX_SOURCES: usize = 16;
pub const MAX_VALUES: usize = 100;
pub const DEPTH: usize = 3;
const TIMEOUT_S: f64 = 5.0;

/// slug: a name of letters, digits, `_` and `-`, 40 at most; "value" where nothing is left.
pub fn slug(text: &str) -> String {
    let mut out = String::new();
    let mut gap = false;
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
            out.push(ch);
            gap = false;
        } else if !gap {
            out.push('_');
            gap = true;
        }
    }
    let cut: String = out.trim_matches('_').chars().take(40).collect();
    match cut.is_empty() {
        true => "value".into(),
        false => cut,
    }
}

/// pick: the value at `path` (`a.b[0].c`), or none where there is none; an error where a
/// list's place is a digit that is no number (Python raises).
pub fn pick<'a>(data: &'a Value, path: &str) -> Result<Option<&'a Value>, String> {
    // `[12]` as `.12`
    let chars: Vec<char> = path.chars().collect();
    let mut plain = String::new();
    let mut at = 0;
    while at < chars.len() {
        if chars[at] == '[' {
            let mut end = at + 1;
            while end < chars.len() && lp_py::decimal(chars[end]).is_some() {
                end += 1;
            }
            if end > at + 1 && chars.get(end) == Some(&']') {
                plain.push('.');
                plain.extend(&chars[at + 1..end]);
                at = end + 1;
                continue;
            }
        }
        plain.push(chars[at]);
        at += 1;
    }
    let mut data = Some(data);
    for step in plain.split('.').filter(|step| !step.is_empty()) {
        data = match data {
            Some(Value::Dict(table)) => table.get(step),
            Some(Value::List(list)) if step.chars().all(lp_py::isdigit) => {
                let place = lp_py::int_of_str(step)
                    .ok_or_else(|| format!("invalid literal for int() with base 10: {step:?}"))?;
                match usize::try_from(place)
                    .ok()
                    .and_then(|place| list.get(place))
                {
                    Some(value) => Some(value),
                    None => return Ok(None),
                }
            }
            _ => return Ok(None),
        };
    }
    Ok(data)
}

/// scalar: a value a reading can hold: a number, a text, true and false as 1 and 0; an error
/// for a whole number too large for a float.
pub fn scalar(value: Option<&Value>) -> Result<Option<Value>, String> {
    Ok(match value {
        Some(Value::Bool(on)) => Some(Value::Float(f64::from(u8::from(*on)))),
        Some(Value::Int(n)) => Some(Value::Float(
            lp_py::num::int_to_float(n).map_err(|_| "int too large to convert to float")?,
        )),
        Some(Value::Float(f)) => Some(Value::Float(*f)),
        Some(Value::Str(text)) => Some(Value::Str(text.chars().take(200).collect())),
        _ => None,
    })
}

/// flatten: every value of `data` down to `depth` levels, by its path with dots.
pub fn flatten(
    data: &Value,
    prefix: &str,
    depth: usize,
) -> Result<IndexMap<String, Value>, String> {
    let mut found = IndexMap::new();
    let items: Vec<(String, &Value)> = match data {
        Value::Dict(table) => table
            .iter()
            .map(|(key, value)| (slug(key), value))
            .collect(),
        Value::List(list) => list
            .iter()
            .enumerate()
            .map(|(n, v)| (n.to_string(), v))
            .collect(),
        other => {
            if let Some(value) = scalar(Some(other))? {
                let key = if prefix.is_empty() { "value" } else { prefix };
                found.insert(key.to_string(), value);
            }
            return Ok(found);
        }
    };
    for (key, value) in items {
        let path = match prefix.is_empty() {
            true => key,
            false => format!("{prefix}.{key}"),
        };
        if matches!(value, Value::Dict(_) | Value::List(_)) {
            if depth > 1 {
                for (inner, got) in flatten(value, &path, depth - 1)? {
                    if found.len() >= MAX_VALUES {
                        return Ok(found);
                    }
                    found.insert(inner, got);
                }
            }
        } else if let Some(got) = scalar(Some(value))? {
            found.insert(path, got);
        }
        if found.len() >= MAX_VALUES {
            break;
        }
    }
    Ok(found)
}

/// One address or file of `sources`.
#[derive(Clone, Debug, PartialEq)]
pub struct Source {
    pub name: String,
    pub url: String,
    pub file: String,
    pub fields: IndexMap<String, String>,
    pub units: IndexMap<String, String>,
    pub headers: IndexMap<String, String>,
}

/// `str(entry.get(key) or "").strip()`.
fn stripped(entry: &Dict, key: &str) -> String {
    match entry.get(key) {
        Some(value) if value.truthy() => lp_py::strip(&value.str()).to_string(),
        _ => String::new(),
    }
}

/// `{name(k): str(v) for k, v in (entry.get(key) or {}).items()}` of a table, else nothing.
fn pairs(entry: &Dict, key: &str, name: fn(&str) -> String) -> IndexMap<String, String> {
    let mut out = IndexMap::new();
    if let Some(Value::Dict(table)) = entry.get(key) {
        for (k, v) in table {
            out.insert(name(k), v.str());
        }
    }
    out
}

impl Source {
    /// The source an entry of `sources` describes; why it is none (Python's ValueError).
    pub fn of(entry: &Dict) -> Result<Source, String> {
        let name = match entry.get("name") {
            Some(value) if value.truthy() => slug(&value.str()),
            _ => "json".to_string(),
        };
        let source = Source {
            url: stripped(entry, "url"),
            file: stripped(entry, "file"),
            fields: pairs(entry, "fields", slug),
            units: pairs(entry, "units", slug),
            headers: pairs(entry, "headers", str::to_string),
            name,
        };
        let named = lp_py::repr(&source.name);
        let web = source.url.starts_with("http://") || source.url.starts_with("https://");
        if !source.url.is_empty() && !web {
            return Err(format!(
                "json source {named}: the url must start with http:// or https://"
            ));
        }
        if source.url.is_empty() && source.file.is_empty() {
            return Err(format!("json source {named}: needs a url or a file"));
        }
        Ok(source)
    }

    /// What the address or the file holds now.
    pub fn load(&self, agent: &str) -> Result<Value, String> {
        let data = if !self.url.is_empty() {
            let mut headers: Vec<(&str, &str)> = vec![("User-Agent", agent)];
            for (name, value) in &self.headers {
                // (one header of a name, the user's own)
                headers.retain(|(known, _)| !known.eq_ignore_ascii_case(name));
                headers.push((name, value));
            }
            lp_net::get(&self.url, &headers, TIMEOUT_S, MAX_BYTES + 1)
                .map_err(|why| why.to_string())?
        } else {
            let mut data = Vec::new();
            std::fs::File::open(expanduser(&self.file)?)
                .and_then(|file| file.take(MAX_BYTES as u64 + 1).read_to_end(&mut data))
                .map_err(|why| why.to_string())?;
            data
        };
        if data.len() > MAX_BYTES {
            return Err("the answer is larger than 1 MB".into());
        }
        // (utf-8-sig: a byte order mark at the start is no part of the text)
        let data = data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&data);
        let text = std::str::from_utf8(data).map_err(|why| why.to_string())?;
        lp_py::json::loads(text).map_err(|why| why.to_string())
    }

    /// The readings of an answer.
    pub fn readings(&self, data: &Value) -> Result<Readings, String> {
        let mut values = IndexMap::new();
        if self.fields.is_empty() {
            values = flatten(data, "", DEPTH)?;
        } else {
            for (field, path) in &self.fields {
                if let Some(value) = scalar(pick(data, path)?)? {
                    values.insert(field.clone(), value);
                }
            }
        }
        let mut out = Readings::new();
        for (field, value) in values {
            let unit = match (&value, self.units.get(&field)) {
                (Value::Float(_), Some(unit)) => unit.clone(),
                _ => String::new(),
            };
            let reading = Reading {
                unit,
                label: format!("{} {field}", self.name),
                ..Reading::of(value)
            };
            out.insert(format!("json.{}.{field}", self.name), reading);
        }
        Ok(out)
    }
}

/// Path.expanduser: `~` at the start as the user's folder.
fn expanduser(path: &str) -> Result<PathBuf, String> {
    let Some(rest) = path.strip_prefix('~') else {
        return Ok(PathBuf::from(path));
    };
    if !(rest.is_empty() || rest.starts_with('/') || (cfg!(windows) && rest.starts_with('\\'))) {
        return Err(format!("another user's folder is not looked up: {path}"));
    }
    let home = match cfg!(windows) {
        true => std::env::var_os("USERPROFILE"),
        false => std::env::var_os("HOME"),
    };
    let home = home.ok_or("Could not determine home directory.")?;
    let rest = rest.trim_start_matches(['/', '\\']);
    Ok(match rest.is_empty() {
        true => PathBuf::from(home),
        false => PathBuf::from(home).join(rest),
    })
}

#[derive(Default)]
struct State {
    stop: bool,
    /// the sources that were asked at least once
    asked: usize,
    /// name -> (when, readings)
    got: IndexMap<String, (Instant, Readings)>,
    /// said once, until it answers again
    failed: Vec<String>,
    notes: Vec<String>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

fn run(shared: &Shared, source: &Source, agent: &str, every: Duration) {
    let mut first = true;
    loop {
        let found = source.load(agent).and_then(|data| source.readings(&data));
        let Ok(mut state) = shared.state.lock() else {
            return;
        };
        let known = state.failed.iter().position(|name| *name == source.name);
        match (found, known) {
            (Ok(readings), known) => {
                if let Some(known) = known {
                    state.failed.remove(known);
                    let note = format!("json: {} answers again", source.name);
                    state.notes.push(note);
                }
                let got = (Instant::now(), readings);
                state.got.insert(source.name.clone(), got);
            }
            (Err(why), None) => {
                state.failed.push(source.name.clone());
                let note = format!("json: {} does not answer: {why}", source.name);
                state.notes.push(note);
            }
            (Err(_), Some(_)) => {}
        }
        if std::mem::take(&mut first) {
            state.asked += 1;
        }
        shared.changed.notify_all();
        let until = Instant::now() + every;
        while !state.stop {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            state = match shared.changed.wait_timeout(state, left) {
                Ok((state, _)) => state,
                Err(_) => return,
            };
        }
        if state.stop {
            return;
        }
    }
}

/// `json.<name>.<field>` from addresses and files (JsonProvider).
pub struct JsonSource {
    shared: Arc<Shared>,
    every: f64,
    threads: Vec<JoinHandle<()>>,
}

impl JsonSource {
    /// The sources `entries` describe (`[[sensors.json.sources]]`), each asked every
    /// `update_s` seconds (two at least) by a program that names itself `agent`.
    pub fn new(entries: &[Value], update_s: f64, agent: &str) -> JsonSource {
        let every = update_s.max(2.0);
        let mut state = State::default();
        let mut sources = Vec::new();
        for (n, entry) in entries.iter().take(MAX_SOURCES).enumerate() {
            match entry.as_dict().map(Source::of) {
                None => {
                    let note = format!("json: sources[{}] is no table, left out", n + 1);
                    state.notes.push(note);
                }
                Some(Err(why)) => state.notes.push(format!("json: {why}")),
                Some(Ok(source)) => sources.push(source),
            }
        }
        let shared = Arc::new(Shared {
            state: Mutex::new(state),
            changed: Condvar::new(),
        });
        let mut threads = Vec::new();
        for source in sources {
            let (theirs, agent) = (Arc::clone(&shared), agent.to_string());
            let wait = Duration::from_secs_f64(every);
            let thread = std::thread::Builder::new()
                .name(format!("sensors-json-{}", source.name))
                .spawn(move || run(&theirs, &source, &agent, wait));
            threads.extend(thread.ok());
        }
        JsonSource {
            shared,
            every,
            threads,
        }
    }

    pub fn read(&mut self) -> Readings {
        let mut out = Readings::new();
        if let Ok(state) = self.shared.state.lock() {
            for (when, readings) in state.got.values() {
                if when.elapsed().as_secs_f64() <= 3.0 * self.every {
                    out.extend(readings.clone());
                }
            }
        }
        out
    }

    /// What the sources said since they were last asked.
    pub fn notes(&self) -> Vec<String> {
        self.shared
            .state
            .lock()
            .map(|mut state| std::mem::take(&mut state.notes))
            .unwrap_or_default()
    }

    /// Waits until every source was asked once, `seconds` at most.
    pub fn wait_ready(&self, seconds: f64) {
        let until = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
        let Ok(mut state) = self.shared.state.lock() else {
            return;
        };
        while state.asked < self.threads.len() {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return;
            }
            state = match self.shared.changed.wait_timeout(state, left) {
                Ok((state, _)) => state,
                Err(_) => return,
            };
        }
    }

    /// Stops asking. The threads end by themselves once an answer under way is in.
    pub fn close(&mut self) {
        if let Ok(mut state) = self.shared.state.lock() {
            state.stop = true;
        }
        self.shared.changed.notify_all();
        self.threads.clear();
    }
}

impl Drop for JsonSource {
    fn drop(&mut self) {
        self.close();
    }
}

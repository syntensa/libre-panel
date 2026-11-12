//! Whom Start tells what is new in a release, and until when (editor/news.py).
//!
//! Someone who used an older Libre Panel is told once, until they close the card; someone
//! whose first Libre Panel is this one gets Start's first steps instead. `news.json` beside
//! config.toml keeps the version that first ran with it (`first`; "older" where config.toml
//! already named a theme when this version first ran) and the release whose news were
//! closed last (`seen`). The file is written when Start first asks, and only where
//! config.toml's folder is there.

use std::path::{Path, PathBuf};

use lp_py::{Dict, Value};

const NEWS_FILENAME: &str = "news.json";
/// `first` of a config.toml that a version from before news.json chose a theme in
const OLDER: &str = "older";

pub struct News {
    path: PathBuf,
    /// worked out as this version starts, before a first start of it chooses a theme
    first: String,
}

impl News {
    pub fn new(config_file: &Path) -> News {
        let folder = config_file.parent().unwrap_or(Path::new("."));
        let first = match crate::pages::theme_chosen(config_file) {
            true => OLDER.to_string(),
            false => lp_py::VERSION.to_string(),
        };
        News {
            path: folder.join(NEWS_FILENAME),
            first,
        }
    }

    fn read(&self) -> Option<(String, String)> {
        let text = std::fs::read(&self.path).ok()?;
        let text = String::from_utf8(text).ok()?;
        let Ok(Value::Dict(data)) = lp_py::json::loads(&text) else {
            return None;
        };
        let first = data.get("first")?.as_str()?.to_string();
        let seen = data.get("seen").and_then(Value::as_str).unwrap_or("");
        Some((first, seen.to_string()))
    }

    fn write(&self, first: &str, seen: &str) {
        let Some(folder) = self.path.parent().filter(|folder| folder.is_dir()) else {
            return; // nothing is there yet (a first start): asked again, it is worked out again
        };
        let told = told(first, seen);
        let text = lp_py::json::dumps(&told, &lp_py::json::Dumps::default()) + "\n";
        // in whole: a read while it is written never finds it empty
        let tmp = folder.join(format!(
            "{NEWS_FILENAME}.{:?}.tmp",
            std::thread::current().id()
        ));
        let done = std::fs::write(&tmp, text).and_then(|()| std::fs::rename(&tmp, &self.path));
        if done.is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
    }

    /// `{"first": version or "older", "seen": release or ""}`, kept from the first ask.
    pub fn state(&self) -> Value {
        let (first, seen) = self.read().unwrap_or_else(|| {
            self.write(&self.first, "");
            (self.first.clone(), String::new())
        });
        told(&first, &seen)
    }

    /// The news of `release` ("0.6") were closed: Start does not show them again.
    pub fn see(&self, release: &str) -> Result<Value, String> {
        let part =
            |text: &str| (1..=4).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_digit());
        let fits = release
            .split_once('.')
            .is_some_and(|(a, b)| part(a) && part(b));
        if !fits {
            return Err("seen is a release such as 0.6".into());
        }
        let Value::Dict(state) = self.state() else {
            unreachable!()
        };
        let first = state.get("first").and_then(Value::as_str).unwrap_or("");
        self.write(first, release);
        Ok(told(first, release))
    }
}

fn told(first: &str, seen: &str) -> Value {
    let mut told = Dict::default();
    told.insert("first".into(), Value::Str(first.into()));
    told.insert("seen".into(), Value::Str(seen.into()));
    Value::Dict(told)
}

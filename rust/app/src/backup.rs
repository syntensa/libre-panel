//! One file with the person's settings and themes, to take to another computer or to keep
//! (backup.py, Settings → System → Backup): a zip of `config.toml` and the folder of the
//! person's own themes, with `libre-panel-backup.json` that says what it is. Restoring checks
//! everything before it writes anything; the config.toml it replaces is kept beside it.

use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use lp_py::{Dict, Value};

use crate::share::{file_mode, local_time, now};
use crate::zip::{Archive, Entry, Info, Writer, ZipError};

const MANIFEST: &str = "libre-panel-backup.json";
const KIND: &str = "libre-panel-backup";
/// the files of a backup together, unpacked
pub const MAX_TOTAL: u64 = 200 * 1024 * 1024;
const MAX_FILES: usize = 5000;
const CONFIG_FILENAME: &str = "config.toml";
const KEPT: &str = "config.toml.before-restore";
/// made again by themselves (drawn layers), or half-written
const LEFT_OUT: [&str; 3] = [".npz", ".tmp", ".pyc"];

/// Why a backup is not made or not put in place.
pub enum Refused {
    /// BackupError (400)
    Backup(String),
    /// OSError (409)
    Os(String),
    /// what Python does not catch: the request is left unanswered
    Failed(String),
}

fn os(e: &std::io::Error, path: &Path) -> Refused {
    Refused::Os(lp_theme::model::os_error_of(e, path).1)
}

fn not_a_backup() -> Refused {
    Refused::Backup(lp_i18n::t("this is not a backup of Libre Panel"))
}

/// The files under `folder`, each by its parts below it, not into linked folders.
fn walk(folder: &Path, parts: &mut Vec<String>, out: &mut Vec<(Vec<String>, PathBuf)>) {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return;
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let path = entry.path();
        parts.push(name);
        out.push((parts.clone(), path.clone()));
        let linked = entry.file_type().is_ok_and(|kind| kind.is_symlink());
        if !linked && path.is_dir() {
            walk(&path, parts, out);
        }
        parts.pop();
    }
}

/// _theme_files: the themes' files, sorted as Python sorts paths, without links, hidden
/// files and what is made again by itself.
fn theme_files(themes: &Path) -> Vec<(Vec<String>, PathBuf)> {
    if !themes.is_dir() {
        return Vec::new();
    }
    let mut all = Vec::new();
    walk(themes, &mut Vec::new(), &mut all);
    let folded = |parts: &[String]| -> Vec<String> {
        match cfg!(windows) {
            true => parts.iter().map(|p| p.to_lowercase()).collect(),
            false => parts.to_vec(),
        }
    };
    all.sort_by_key(|(parts, _)| folded(parts));
    all.into_iter()
        .filter(|(parts, path)| {
            let linked = std::fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink());
            let name = parts.last().map_or("", String::as_str);
            let suffix = crate::server::suffix(name);
            path.is_file()
                && !linked
                && !LEFT_OUT.contains(&suffix)
                && !parts
                    .iter()
                    .any(|part| part.starts_with('.') || part == "__pycache__")
        })
        .collect()
}

/// ZipInfo.from_file's time of a file (strict: none before 1980).
fn file_time(meta: &std::fs::Metadata) -> Result<[u32; 6], Refused> {
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0.0, |d| d.as_secs_f64());
    let at = local_time(mtime);
    if at[0] < 1980 {
        return Err(Refused::Failed(
            "ValueError: ZIP does not support timestamps before 1980".into(),
        ));
    }
    Ok(at)
}

/// make_backup: the backup of this computer's settings and own themes, as the bytes of a zip.
pub fn make_backup(config_path: &Path, themes: &Path) -> Result<Vec<u8>, Refused> {
    let files = theme_files(themes);
    let mut names: Vec<String> = files.iter().map(|(parts, _)| parts[0].clone()).collect();
    names.sort();
    names.dedup();
    let mut manifest = Dict::default();
    manifest.insert("kind".into(), Value::from(KIND));
    manifest.insert("version".into(), Value::from(lp_py::VERSION));
    let at = local_time(now());
    let made = format!(
        "{:04}-{:02}-{:02} {:02}:{:02}",
        at[0], at[1], at[2], at[3], at[4]
    );
    manifest.insert("made".into(), Value::Str(made));
    manifest.insert(
        "themes".into(),
        Value::List(names.into_iter().map(Value::Str).collect()),
    );
    let how = lp_py::json::Dumps {
        indent: Some("  ".into()),
        ..lp_py::json::Dumps::default()
    };
    let text = lp_py::json::dumps(&Value::Dict(manifest), &how);
    let mut zip = Writer::default();
    zip.add(&Entry {
        name: MANIFEST,
        date_time: local_time(now()),
        external_attr: 0o600 << 16,
        data: text.as_bytes(),
    });
    if config_path.is_file() {
        let meta = std::fs::metadata(config_path).map_err(|e| os(&e, config_path))?;
        let date_time = file_time(&meta)?;
        let body = std::fs::read(config_path).map_err(|e| os(&e, config_path))?;
        zip.add(&Entry {
            name: CONFIG_FILENAME,
            date_time,
            external_attr: file_mode(&meta) << 16,
            data: &body,
        });
    }
    for (parts, path) in &files {
        let meta = std::fs::metadata(path).map_err(|e| os(&e, path))?;
        let date_time = file_time(&meta)?;
        let body = std::fs::read(path).map_err(|e| os(&e, path))?;
        let name = format!("themes/{}", parts.join("/"));
        zip.add(&Entry {
            name: &name,
            date_time,
            external_attr: file_mode(&meta) << 16,
            data: &body,
        });
    }
    Ok(zip.finish())
}

/// json.loads of bytes: the encoding told by its first bytes (json.detect_encoding).
fn json_of(data: &[u8]) -> Option<Value> {
    let units16 = |data: &[u8], big: bool| -> Option<String> {
        let units: Vec<u16> = data
            .chunks(2)
            .map(|c| match (c, big) {
                ([a, b], true) => Some(u16::from_be_bytes([*a, *b])),
                ([a, b], false) => Some(u16::from_le_bytes([*a, *b])),
                _ => None,
            })
            .collect::<Option<_>>()?;
        char::decode_utf16(units)
            .collect::<Result<String, _>>()
            .ok()
    };
    let units32 = |data: &[u8], big: bool| -> Option<String> {
        data.chunks(4)
            .map(|c| {
                let word: [u8; 4] = c.try_into().ok()?;
                let n = match big {
                    true => u32::from_be_bytes(word),
                    false => u32::from_le_bytes(word),
                };
                char::from_u32(n)
            })
            .collect()
    };
    let text = if data.starts_with(&[0, 0, 0xFE, 0xFF]) {
        units32(&data[4..], true)
    } else if data.starts_with(&[0xFF, 0xFE, 0, 0]) {
        units32(&data[4..], false)
    } else if data.starts_with(&[0xFE, 0xFF]) {
        units16(&data[2..], true)
    } else if data.starts_with(&[0xFF, 0xFE]) {
        units16(&data[2..], false)
    } else if let Some(rest) = data.strip_prefix(&[0xEF, 0xBB, 0xBF]) {
        String::from_utf8(rest.to_vec()).ok()
    } else if data.len() >= 4 && data[0] == 0 {
        match data[1] != 0 {
            true => units16(data, true),
            false => units32(data, true),
        }
    } else if data.len() >= 4 && data[1] == 0 {
        match data[2] != 0 || data[3] != 0 {
            true => units16(data, false),
            false => units32(data, false),
        }
    } else if data.len() == 2 && data[0] == 0 {
        units16(data, true)
    } else if data.len() == 2 && data[1] == 0 {
        units16(data, false)
    } else {
        String::from_utf8(data.to_vec()).ok()
    };
    lp_py::json::loads(&text?).ok()
}

/// PurePosixPath(name).parts
fn posix_parts(name: &str) -> Vec<&str> {
    let (root, rest) = if name.starts_with("//") && !name.starts_with("///") {
        (Some("//"), &name[2..])
    } else if name.starts_with('/') {
        (Some("/"), name.trim_start_matches('/'))
    } else {
        (None, name)
    };
    root.into_iter()
        .chain(
            rest.split('/')
                .filter(|part| !part.is_empty() && *part != "."),
        )
        .collect()
}

/// A zip's error inside restore_backup: BadZipFile is no backup; anything else is not caught.
fn zip_refused(e: ZipError) -> Refused {
    match e {
        ZipError::BadZip(_) => not_a_backup(),
        other => Refused::Failed(other.told()),
    }
}

type Found = (Option<Vec<u8>>, IndexMap<String, Vec<(String, Vec<u8>)>>);

/// _checked: what a backup holds, read and checked: config.toml, and the files of each theme
/// by its name. Nothing is written yet.
fn checked(archive: &Archive) -> Result<Found, Refused> {
    let entries = &archive.infolist;
    if entries.len() > MAX_FILES {
        return Err(not_a_backup()); // (that many files)
    }
    if entries
        .iter()
        .map(|e| u128::from(e.file_size))
        .sum::<u128>()
        > u128::from(MAX_TOTAL)
    {
        return Err(Refused::Backup(lp_i18n::t("the backup is too large")));
    }
    let named = |name: &str| -> Option<&Info> { entries.iter().rev().find(|e| e.filename == name) };
    let Some(manifest) = named(MANIFEST) else {
        return Err(not_a_backup());
    };
    let manifest = json_of(&archive.read(manifest).map_err(zip_refused)?);
    match manifest {
        Some(Value::Dict(d)) if d.get("kind") == Some(&Value::from(KIND)) => {}
        _ => return Err(not_a_backup()),
    }
    let mut config = None;
    let mut themes: IndexMap<String, Vec<(String, Vec<u8>)>> = IndexMap::new();
    for entry in entries {
        let name = entry.filename.as_str();
        if entry.is_dir() || name == MANIFEST {
            continue;
        }
        if name == CONFIG_FILENAME {
            config = Some(archive.read(entry).map_err(zip_refused)?);
            continue;
        }
        let parts = posix_parts(name);
        let wrong = parts.len() < 3
            || parts[0] != "themes"
            || name.contains('\\')
            || name.starts_with('/')
            || parts
                .iter()
                .any(|part| ["", ".", ".."].contains(part) || part.contains(':'))
            || !lp_theme::model::valid_theme_name(parts[1]);
        if wrong {
            let said = lp_i18n::t_format(
                "{name}: not a file a backup of Libre Panel holds",
                &[("name", Value::from(name))],
            )
            .unwrap_or_else(|why| why.msg);
            return Err(Refused::Backup(said));
        }
        let body = archive.read(entry).map_err(zip_refused)?;
        themes
            .entry(parts[1].to_string())
            .or_default()
            .push((parts[2..].join("/"), body));
    }
    if let Some(bytes) = &config {
        let cannot = |error: String| {
            let said = lp_i18n::t_format(
                "its config.toml cannot be read: {error}",
                &[("error", Value::Str(error))],
            );
            Refused::Backup(said.unwrap_or_else(|why| why.msg))
        };
        let text = lp_py::codecs::decode_utf8(bytes).map_err(|e| cannot(e.to_string()))?;
        let table = lp_py::toml::loads(text).map_err(|e| match e.kind {
            lp_py::toml::ErrorKind::Recursion => {
                Refused::Failed(format!("RecursionError: {}", e.msg))
            }
            _ => cannot(e.msg),
        })?;
        lp_config::parse_config(&table, None, lp_config::platform()).map_err(|e| {
            match e.class {
                "ConfigError" | "ValueError" | "UnicodeDecodeError" => cannot(e.msg),
                class => Refused::Failed(format!("{class}: {}", e.msg)),
            }
        })?;
    }
    Ok((config, themes))
}

/// restore_backup: a backup put in place: its config.toml (the one before kept beside it)
/// and its themes; `{"config": whether it had one, "themes": the names}`.
pub fn restore_backup(data: &[u8], config_path: &Path, themes: &Path) -> Result<Value, Refused> {
    let archive = Archive::new(data).map_err(zip_refused)?;
    let (config, found) = checked(&archive)?;
    for (name, files) in &found {
        let folder = themes.join(name);
        for (relative, content) in files {
            let target = folder.join(relative);
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent).map_err(|e| os(&e, parent))?;
            }
            std::fs::write(&target, content).map_err(|e| os(&e, &target))?;
        }
    }
    if let Some(bytes) = &config {
        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| os(&e, parent))?;
        }
        if config_path.is_file() {
            let before = std::fs::read(config_path).map_err(|e| os(&e, config_path))?;
            let kept = config_path.with_file_name(KEPT);
            std::fs::write(&kept, before).map_err(|e| os(&e, &kept))?;
        }
        let text = String::from_utf8_lossy(bytes);
        lp_config::edit::write_text(config_path, &text).map_err(|e| Refused::Os(e.msg))?;
    }
    let mut names: Vec<&String> = found.keys().collect();
    names.sort();
    let mut told = Dict::default();
    told.insert("config".into(), Value::Bool(config.is_some()));
    told.insert(
        "themes".into(),
        Value::List(names.into_iter().map(|n| Value::Str(n.clone())).collect()),
    );
    Ok(Value::Dict(told))
}

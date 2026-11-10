//! A theme as one file to give to someone, and such a file put in place (theme/share.py:
//! Start → Themes: Export…, Import…). The file is a zip of the theme's folder:
//! `<name>/theme.json` and the pictures and fonts the theme names, nothing else; its
//! theme.json without a line that names one of the person's folders. Importing trusts nothing
//! in the file: its size, its paths, links, kinds of files and their first bytes, and the
//! theme.json are checked before the theme is put in the person's themes folder.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use indexmap::IndexMap;
use lp_py::{Dict, Value};
use lp_theme::versions::{self, Places};
use lp_theme::{Env, Kind};

use crate::zip::{Archive, Entry, Info, Writer, ZipError};

pub const MAX_ZIP: u64 = 20 * 1024 * 1024; // a shared theme's file
const MAX_UNPACKED: u64 = 50 * 1024 * 1024; // its files together
const MAX_FILES: usize = 1000;
const MAX_THEME_JSON: u64 = 2 * 1024 * 1024;
const MAX_GLOBS: usize = 64; // "photos/*.jpg": a folder of pictures
const THEME_FILENAME: &str = lp_theme::tables::THEME_FILENAME;

/// What a theme folder may carry besides its theme.json, by the first bytes of each kind.
const MAGIC: &[(&str, &[&[u8]])] = &[
    (".png", &[b"\x89PNG"]),
    (".jpg", &[b"\xff\xd8\xff"]),
    (".jpeg", &[b"\xff\xd8\xff"]),
    (".gif", &[b"GIF87a", b"GIF89a"]),
    (".webp", &[b"RIFF"]),
    (".ttf", &[b"\x00\x01\x00\x00", b"true", b"OTTO"]),
    (".otf", &[b"OTTO", b"\x00\x01\x00\x00", b"true"]),
    // videos: an MP4 or MOV says its kind at 4, WebM and MKV are EBML
    (".mp4", &[b""]),
    (".mov", &[b""]),
    (".m4v", &[b""]),
    (".webm", &[b"\x1aE\xdf\xa3"]),
    (".mkv", &[b"\x1aE\xdf\xa3"]),
];
const BOXES: &[&[u8]] = &[b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"];
/// what the file managers of macOS and Windows put into a zip by themselves: left out
const LITTER: &[&str] = &[".DS_Store", "Thumbs.db", "desktop.ini"];

/// one import at a time picks a free name and takes it
static TAKING: Mutex<()> = Mutex::new(());

/// Why a theme was not shared or put in place, as the server answers it.
#[derive(Debug)]
pub enum Refused {
    /// ShareError: 400
    Share(String),
    /// ThemeError: the theme is not there or does not load (404)
    Theme(String),
    /// OSError putting it in place (409)
    Os(String),
    /// what Python does not catch there: "Class: message" (500)
    Failed(String),
}

type Answer<T> = Result<T, Refused>;

fn said(text: &str, values: &[(&str, Value)]) -> String {
    lp_i18n::t_format(text, values).unwrap_or_else(|why| why.msg)
}

fn mb(size: u64) -> Value {
    Value::int((size / (1024 * 1024)) as i64)
}

fn not_a_zip() -> Refused {
    Refused::Share(lp_i18n::t(
        "This file is not a .zip: a theme is shared as a .zip of its folder.",
    ))
}

fn failed(kind: &Kind, msg: &str) -> Refused {
    Refused::Failed(format!("{}: {msg}", kind.name()))
}

fn os_refused(e: &std::io::Error, path: &Path) -> Refused {
    Refused::Os(lp_theme::model::os_error_of(e, path).1)
}

/// What reading the zip came to: no zip (zipfile's errors, a damaged entry) or what Python
/// does not catch.
fn unread(e: ZipError) -> Refused {
    match e {
        ZipError::BadZip(_) | ZipError::Eof | ZipError::Zlib(_) => not_a_zip(),
        other => Refused::Failed(other.told()),
    }
}

/// PurePath(name).suffix, lower case (on Windows its backslashes part it too).
fn suffix_lower(name: &str) -> String {
    let path = lp_services::paths::Path::new(cfg!(windows), name);
    lp_py::lower(path.suffix())
}

fn is_file_type(suffix: &str) -> bool {
    MAGIC.iter().any(|(kind, _)| *kind == suffix)
}

/// _lines: every line of every text in theme.json's content, once.
fn lines(data: &Value, found: &mut std::collections::BTreeSet<String>) {
    match data {
        Value::Dict(d) => d.values().for_each(|v| lines(v, found)),
        Value::List(l) => l.iter().for_each(|v| lines(v, found)),
        Value::Str(s) => {
            for line in lp_py::splitlines(s) {
                let line = lp_py::strip(line);
                if !line.is_empty() {
                    found.insert(line.to_string());
                }
            }
        }
        _ => {}
    }
}

/// _own_file: `name` as a file of the theme folder: a picture or font inside it, reached
/// without a link, by the path it has there ("assets/back.png"); None for anything else.
fn own_file(folder: &Path, base: &Path, name: &str) -> Option<String> {
    if name.chars().count() > 255 || !is_file_type(&suffix_lower(name)) {
        return None;
    }
    let target = lp_theme::model::resolve_asset(folder, &Value::Str(name.to_string())).ok()?;
    if !target.is_file() {
        return None;
    }
    let rel = target.strip_prefix(base).ok()?;
    let found: Vec<String> = rel
        .components()
        .map(|c| c.as_os_str().to_str().map(str::to_string))
        .collect::<Option<_>>()?;
    let found = found.join("/");
    let named = lp_services::paths::normpath(false, &name.replace('\\', "/"));
    // a link inside the folder resolves to another path than the one named: left out
    let same = lp_py::fnmatch::normcase(&found) == lp_py::fnmatch::normcase(&named);
    same.then_some(named)
}

/// The parts of a pattern as pathlib's glob takes them; None where it refuses it.
fn pattern_parts(pattern: &str) -> Option<Vec<String>> {
    let sep = |c: char| c == '/' || (cfg!(windows) && c == '\\');
    let (drive, root, _) = lp_services::paths::splitroot(cfg!(windows), pattern);
    if pattern.is_empty() || !drive.is_empty() || !root.is_empty() {
        return None;
    }
    let mut parts: Vec<String> = pattern
        .split(sep)
        .filter(|p| !p.is_empty() && *p != ".")
        .map(str::to_string)
        .collect();
    if pattern.ends_with(sep) {
        parts.push(String::new());
    }
    if parts.is_empty() || parts.iter().any(|p| p != "**" && p.contains("**")) {
        return None;
    }
    Some(parts)
}

/// A part's match of a name: fnmatch's; without case on Windows (re.IGNORECASE).
fn part_matches(name: &str, part: &str) -> bool {
    let matched = if cfg!(windows) {
        lp_py::fnmatch::fnmatchcase(&name.to_lowercase(), &part.to_lowercase())
    } else {
        lp_py::fnmatch::fnmatchcase(name, part)
    };
    matched.unwrap_or(false)
}

/// The folders below `path` (relative parts), itself first, top down, no link followed.
fn folders_below(root: &Path, rel: &[String], out: &mut Vec<Vec<String>>) {
    out.push(rel.to_vec());
    let here = rel.iter().fold(root.to_path_buf(), |p, part| p.join(part));
    let Ok(entries) = std::fs::read_dir(here) else {
        return;
    };
    for entry in entries.flatten() {
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            let mut below = rel.to_vec();
            below.push(name);
            folders_below(root, &below, out);
        }
    }
}

/// pathlib's selectors (Python 3.12) from `rel` below `root`.
fn select(root: &Path, rel: &[String], parts: &[String], out: &mut Vec<Vec<String>>) {
    let Some((part, rest)) = parts.split_first() else {
        out.push(rel.to_vec());
        return;
    };
    if part.is_empty() {
        out.push(rel.to_vec());
        return;
    }
    if part == "**" {
        let skip = parts.iter().take_while(|p| *p == "**").count();
        let child = &parts[skip..];
        let mut starts = Vec::new();
        folders_below(root, rel, &mut starts);
        let mut found = Vec::new();
        for start in &starts {
            select(root, start, child, &mut found);
        }
        out.extend(found);
        return;
    }
    let mut next = rel.to_vec();
    if part == ".." {
        next.push("..".into());
        select(root, &next, rest, out);
        return;
    }
    let here = rel.iter().fold(root.to_path_buf(), |p, part| p.join(part));
    let Ok(entries) = std::fs::read_dir(&here) else {
        return;
    };
    for entry in entries.flatten() {
        if !rest.is_empty() && !std::fs::metadata(entry.path()).is_ok_and(|m| m.is_dir()) {
            continue;
        }
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if part_matches(&name, part) {
            let mut below = rel.to_vec();
            below.push(name);
            select(root, &below, rest, out);
        }
    }
}

/// folder.glob(pattern), each path below the folder as text; None where pathlib refuses.
fn glob(folder: &Path, pattern: &str) -> Option<Vec<String>> {
    let parts = pattern_parts(pattern)?;
    if !folder.is_dir() {
        return Some(Vec::new());
    }
    let mut found = Vec::new();
    select(folder, &[], &parts, &mut found);
    Some(found.into_iter().map(|parts| parts.join("/")).collect())
}

/// referenced_files: the files in `folder` that the theme `data` names (an image's `src`, a
/// font, a line of a slideshow, `photos/*.jpg` for a folder of them), as paths inside the
/// folder, sorted. Nothing outside it, nothing reached through a link, nothing of another
/// kind.
pub fn referenced_files(folder: &Path, data: &Value) -> Vec<String> {
    let base = lp_theme::paths::resolve(folder).unwrap_or_else(|_| folder.to_path_buf());
    let mut all = std::collections::BTreeSet::new();
    lines(data, &mut all);
    let mut sorted: Vec<String> = all.into_iter().collect();
    sorted.sort_by(|a, b| a.chars().cmp(b.chars()));
    let mut found = std::collections::BTreeSet::new();
    let mut globs = 0;
    for line in sorted {
        let mut names = vec![line.clone()];
        if line.contains(['*', '?', '[']) {
            globs += 1;
            if globs > MAX_GLOBS {
                continue;
            }
            match glob(folder, &line) {
                Some(matched) => names = matched,
                None => continue,
            }
        }
        for name in names {
            if let Some(own) = own_file(folder, &base, &name) {
                found.insert(own);
            }
        }
    }
    let mut found: Vec<String> = found.into_iter().collect();
    found.sort_by(|a, b| a.chars().cmp(b.chars()));
    found
}

/// _private_folders: the person's own folders as a text may name them: their home and the
/// settings folder, in small letters (casefold).
fn private_folders() -> Vec<String> {
    let mut folders = vec![lp_config::dirs::config_dir()];
    if let Some(home) = home_folder() {
        folders.push(home);
    }
    let mut names = std::collections::BTreeSet::new();
    for folder in folders {
        // (str(folder) and folder.as_posix(): on Windows with slashes too)
        let text = folder.to_string_lossy().into_owned();
        let posix = if cfg!(windows) {
            text.replace('\\', "/")
        } else {
            text.clone()
        };
        for text in [text, posix] {
            if text.chars().count() > 3 {
                names.insert(lp_py::casefold(&text));
            }
        }
    }
    let mut names: Vec<String> = names.into_iter().collect();
    names.sort_by(|a, b| a.chars().cmp(b.chars()));
    names
}

/// Path.home(): HOME (USERPROFILE on Windows), else the system's account.
fn home_folder() -> Option<PathBuf> {
    let named = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    std::env::var_os(named)
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            #[allow(deprecated)]
            std::env::home_dir()
        })
}

/// _whole_path: whether a line names a whole path (or one that climbs out).
fn whole_path(line: &str) -> bool {
    if line.starts_with(['/', '\\', '~']) {
        return true;
    }
    if !lp_services::paths::splitroot(true, line).0.is_empty() {
        return true;
    }
    line.split(['/', '\\']).any(|part| part == "..")
}

/// _scrubbed: theme.json's content without a line that names one of the person's folders,
/// and in the lists of pictures without one that names a whole path.
fn scrubbed(value: &Value, private: &[String], pictures: bool) -> Value {
    match value {
        Value::Dict(d) => {
            let text = |key: &str| match d.get(key) {
                Some(Value::Str(s)) => s.as_str(),
                _ => "",
            };
            let names: &[&str] = match (text("type"), text("module")) {
                ("image", _) => &["slides"],
                ("module", "image") => &["items"],
                _ => &[],
            };
            let out: Dict = d
                .iter()
                .map(|(k, v)| (k.clone(), scrubbed(v, private, names.contains(&k.as_str()))))
                .collect();
            Value::Dict(out)
        }
        Value::List(l) => Value::List(l.iter().map(|v| scrubbed(v, private, false)).collect()),
        Value::Str(s) => {
            let kept = |line: &&str| {
                let folded = lp_py::casefold(line);
                if private.iter().any(|name| folded.contains(name.as_str())) {
                    return false;
                }
                let line = lp_py::strip(line);
                !pictures || line.starts_with('@') || !whole_path(line)
            };
            Value::Str(s.split('\n').filter(kept).collect::<Vec<_>>().join("\n"))
        }
        other => other.clone(),
    }
}

/// time.localtime(seconds)[:6].
fn local_time(seconds: f64) -> [u32; 6] {
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    match lp_py::zones::fromtimestamp(zone, seconds.floor()) {
        Some((t, _)) => [
            t.year.clamp(0, 9999) as u32,
            t.month,
            t.day,
            t.hour,
            t.minute,
            t.second,
        ],
        None => [1980, 1, 1, 0, 0, 0],
    }
}

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

/// The bits of st_mode Python's ZipInfo.from_file keeps (on Windows those os.stat makes up).
fn file_mode(meta: &std::fs::Metadata) -> u32 {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        meta.mode() & 0xFFFF
    }
    #[cfg(not(unix))]
    {
        let write = if meta.permissions().readonly() {
            0
        } else {
            0o222
        };
        0o100000 | 0o444 | write
    }
}

/// export_theme: the theme `name` as the bytes of a zip to share, and the zip's file name.
pub fn export_theme(name: &str, places: &Places, env: &Env) -> Answer<(Vec<u8>, String)> {
    let theme = versions::find_theme(name, places)
        .and_then(|folder| Ok((lp_theme::load_theme(&folder, env)?, folder)));
    let (theme, folder) = match theme {
        Ok(found) => found,
        Err(e) if e.kind == Kind::Theme => return Err(Refused::Theme(e.msg)),
        Err(e) => return Err(failed(&e.kind, &e.msg)),
    };
    let data = scrubbed(&theme.to_dict(), &private_folders(), false);
    let files = referenced_files(&folder, &data);
    let mut size = 0u64;
    for rel in &files {
        let path = folder.join(rel);
        let meta = std::fs::metadata(&path).map_err(|e| {
            let (class, msg) = lp_theme::model::os_error_of(&e, &path);
            Refused::Failed(format!("{class}: {msg}"))
        })?;
        size += meta.len();
    }
    if size > MAX_UNPACKED {
        return Err(Refused::Share(said(
            "The files of “{name}” come to more than {size} MB: too much to share as one file.",
            &[
                ("name", Value::Str(name.into())),
                ("size", mb(MAX_UNPACKED)),
            ],
        )));
    }
    let mut zip = Writer::default();
    let dumps = lp_py::json::Dumps {
        indent: Some("  ".into()),
        ensure_ascii: false,
        ..Default::default()
    };
    let text = lp_py::json::dumps(&data, &dumps) + "\n";
    let arcname = format!("{name}/{THEME_FILENAME}");
    zip.add(&Entry {
        name: &arcname,
        date_time: local_time(now()),
        external_attr: 0o600 << 16,
        data: text.as_bytes(),
    });
    for rel in &files {
        let path = folder.join(rel);
        let failed_os = |e: std::io::Error| {
            let (class, msg) = lp_theme::model::os_error_of(&e, &path);
            Refused::Failed(format!("{class}: {msg}"))
        };
        let meta = std::fs::metadata(&path).map_err(failed_os)?;
        let body = std::fs::read(&path).map_err(failed_os)?;
        let mtime = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0.0, |d| d.as_secs_f64());
        // (a file dated before 1980, as some packages date theirs, is dated 1980)
        let mut date_time = local_time(mtime);
        if date_time[0] < 1980 {
            date_time = [1980, 1, 1, 0, 0, 0];
        } else if date_time[0] > 2107 {
            date_time = [2107, 12, 31, 23, 59, 59];
        }
        let arcname = format!("{name}/{rel}");
        zip.add(&Entry {
            name: &arcname,
            date_time,
            external_attr: file_mode(&meta) << 16,
            data: &body,
        });
    }
    Ok((zip.finish(), format!("{name}.zip")))
}

/// _checked: what a shared theme's zip holds, checked: the folder its files are in ("" for
/// none), and its files by their path inside the theme. Nothing is written.
fn checked(archive: &Archive) -> Answer<(String, IndexMap<String, Info>)> {
    let entries = &archive.infolist;
    if entries.len() > MAX_FILES {
        return Err(Refused::Share(said(
            "This .zip holds more than {count} files: too many for a theme.",
            &[("count", Value::int(MAX_FILES as i64))],
        )));
    }
    if entries
        .iter()
        .map(|e| u128::from(e.file_size))
        .sum::<u128>()
        > u128::from(MAX_UNPACKED)
    {
        return Err(Refused::Share(said(
            "The theme's files come to more than {size} MB unpacked: a shared theme may be at \
             most that.",
            &[("size", mb(MAX_UNPACKED))],
        )));
    }
    let named = |text: &str, name: &str| {
        Refused::Share(said(text, &[("name", Value::Str(name.to_string()))]))
    };
    let mut files: IndexMap<String, Info> = IndexMap::new();
    for entry in entries {
        let name = &entry.filename;
        let parts: Vec<&str> = name.trim_end_matches('/').split('/').collect();
        let odd = |part: &&str| ["", ".", ".."].contains(part) || part.contains(':');
        if name.contains('\\') || parts.iter().any(odd) {
            return Err(named(
                "“{name}” in the .zip leaves the theme's folder or names a whole path: nothing \
                 was imported.",
                name,
            ));
        }
        if (entry.external_attr >> 16) & 0o170000 == 0o120000 {
            return Err(named(
                "“{name}” in the .zip is a link to another place: nothing was imported.",
                name,
            ));
        }
        if entry.flag_bits & 0x1 != 0 {
            return Err(named(
                "“{name}” in the .zip is locked with a password: nothing was imported.",
                name,
            ));
        }
        let last = parts.last().copied().unwrap_or("");
        if entry.is_dir() || parts[0] == "__MACOSX" || LITTER.contains(&last) {
            continue;
        }
        files.insert(name.clone(), entry.clone());
    }
    let top = if files.contains_key(THEME_FILENAME) {
        String::new()
    } else {
        let tops: std::collections::HashSet<&str> = files
            .keys()
            .map(|n| n.split('/').next().unwrap_or(""))
            .collect();
        let top = match tops.len() {
            1 => tops.into_iter().next().unwrap_or("").to_string(),
            _ => String::new(),
        };
        if top.is_empty() || !files.contains_key(&format!("{top}/{THEME_FILENAME}")) {
            return Err(Refused::Share(lp_i18n::t(
                "There is no theme.json in this .zip: a theme is shared as a .zip of its \
                 folder, with its theme.json.",
            )));
        }
        top
    };
    let inside: IndexMap<String, Info> = files
        .into_iter()
        .map(|(name, entry)| {
            let rel = match top.is_empty() {
                true => name,
                false => name.chars().skip(top.chars().count() + 1).collect(),
            };
            (rel, entry)
        })
        .collect();
    for (rel, entry) in &inside {
        let suffix = lp_py::lower(lp_services::paths::Path::new(false, rel).suffix());
        if rel != THEME_FILENAME && !is_file_type(&suffix) {
            return Err(named(
                "“{name}” is not a file a theme carries (theme.json, pictures: PNG, JPEG, GIF, \
                 WebP, videos: MP4, WebM, MOV, fonts: TTF, OTF): nothing was imported.",
                &entry.filename,
            ));
        }
    }
    Ok((top, inside))
}

/// _what_it_says: whether a picture, a video or a font is the kind its name says.
fn what_it_says(rel: &str, content: &[u8]) -> bool {
    let suffix = lp_py::lower(lp_services::paths::Path::new(false, rel).suffix());
    let Some((_, starts)) = MAGIC.iter().find(|(kind, _)| *kind == suffix) else {
        return false;
    };
    if !starts.iter().any(|start| content.starts_with(start)) {
        return false;
    }
    if [".mp4", ".mov", ".m4v"].contains(&suffix.as_str()) {
        return content.get(4..8).is_some_and(|b| BOXES.contains(&b));
    }
    suffix != ".webp" || content.get(8..12) == Some(b"WEBP")
}

/// _slug: a name made of a text: small letters, digits, ".", "_" and "-", at most 64.
fn slug(text: &str) -> String {
    let lowered = lp_py::lower(text);
    let mut out = String::new();
    let mut gap = false;
    for c in lowered.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-') {
            out.push(c);
            gap = false;
        } else if !gap {
            out.push('-');
            gap = true;
        }
    }
    let trimmed: String = out.trim_matches(['-', '.']).chars().take(64).collect();
    let slug = trimmed.trim_matches(['-', '.']).to_string();
    if lp_theme::model::valid_theme_name(&slug) {
        slug
    } else {
        String::new()
    }
}

fn taken(name: &str, themes: &Path, places: &Places) -> bool {
    // (a built-in or a plugin's theme of that name: yours would hide it)
    themes.join(name).exists() || versions::find_theme(name, places).is_ok()
}

/// _free_name: `wanted`, or with the first number after it that makes it free (`lumen-2`).
fn free_name(wanted: &str, themes: &Path, places: &Places) -> String {
    let (mut name, mut n) = (wanted.to_string(), 1);
    while taken(&name, themes, places) {
        n += 1;
        let ending = format!("-{n}");
        let keep = 64usize.saturating_sub(ending.chars().count());
        let stem: String = wanted.chars().take(keep).collect();
        name = format!("{}{ending}", stem.trim_end_matches(['-', '.']));
    }
    name
}

fn token_hex(n: usize) -> String {
    let mut bytes = vec![0u8; n];
    let _ = getrandom::getrandom(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The folders below `folder` (itself too), as os.walk finds them.
fn walked(folder: &Path, out: &mut Vec<PathBuf>) {
    out.push(folder.to_path_buf());
    if let Ok(entries) = std::fs::read_dir(folder) {
        for entry in entries.flatten() {
            if entry.file_type().is_ok_and(|t| t.is_dir()) {
                walked(&entry.path(), out);
            }
        }
    }
}

/// What an import put in place: `{"id", "name", "files"}`.
pub struct Imported {
    pub id: String,
    pub name: String,
    pub files: usize,
}

/// import_theme: a shared theme (the bytes of its zip) put into the person's themes folder
/// `themes`, named `folder`, else after the one in the zip, else `name` (the file's), else
/// the theme's name, with a number where it is taken. Nothing is in place where the file is
/// not a sound shared theme.
pub fn import_theme(
    data: &[u8],
    themes: &Path,
    places: &Places,
    env: &Env,
    name: &str,
    folder: &str,
) -> Answer<Imported> {
    if data.len() as u64 > MAX_ZIP {
        return Err(Refused::Share(said(
            "The file is larger than {size} MB: a shared theme may be at most that.",
            &[("size", mb(MAX_ZIP))],
        )));
    }
    let archive = match Archive::new(data) {
        Ok(archive) => archive,
        Err(ZipError::BadZip(_) | ZipError::UnicodeDecode(_)) => return Err(not_a_zip()),
        Err(other) => return Err(Refused::Failed(other.told())),
    };
    let staging = themes.join(format!(".import-{}", token_hex(6))); // hidden: no list shows it
    let done = put_in_place(&archive, &staging, themes, places, env, name, folder);
    if staging.exists() {
        let _ = std::fs::remove_dir_all(&staging);
    }
    done
}

fn put_in_place(
    archive: &Archive,
    staging: &Path,
    themes: &Path,
    places: &Places,
    env: &Env,
    name: &str,
    folder: &str,
) -> Answer<Imported> {
    let (top, files) = checked(archive)?;
    let entry = &files[THEME_FILENAME];
    if entry.file_size > MAX_THEME_JSON {
        return Err(Refused::Share(said(
            "Its theme.json is larger than {size} MB: more than a theme needs.",
            &[("size", mb(MAX_THEME_JSON))],
        )));
    }
    let bytes = archive.read(entry).map_err(unread)?;
    let unreadable = |error: String| {
        Refused::Share(said(
            "Its theme.json cannot be read: {error}",
            &[("error", Value::Str(error))],
        ))
    };
    let text = lp_py::codecs::decode_utf8(&bytes).map_err(|e| unreadable(e.to_string()))?;
    let raw = lp_py::json::loads(text).map_err(|e| unreadable(e.to_string()))?;
    let theme = match lp_theme::parse_theme(&raw, Some(staging), env) {
        Ok(theme) => theme,
        Err(e) if matches!(e.kind, Kind::Theme | Kind::Recursion) => {
            return Err(Refused::Share(said(
                "Its theme.json is not a theme Libre Panel can show: {error}",
                &[("error", Value::Str(e.msg))],
            )))
        }
        Err(e) => return Err(failed(&e.kind, &e.msg)),
    };
    std::fs::create_dir_all(staging).map_err(|e| os_refused(&e, staging))?;
    for (rel, info) in &files {
        let target = staging.join(rel);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(|e| os_refused(&e, parent))?;
        }
        let body = archive.read(info).map_err(unread)?;
        std::fs::write(&target, body).map_err(|e| os_refused(&e, &target))?;
    }
    let kept = referenced_files(staging, &raw);
    for rel in files.keys() {
        let path = staging.join(rel);
        if rel == THEME_FILENAME || kept.contains(rel) {
            if rel != THEME_FILENAME {
                let content = std::fs::read(&path).map_err(|e| os_refused(&e, &path))?;
                if !what_it_says(rel, &content) {
                    return Err(Refused::Share(said(
                        "“{name}” is not the picture or font its name says: nothing was \
                         imported.",
                        &[("name", Value::Str(rel.clone()))],
                    )));
                }
            }
            continue;
        }
        std::fs::remove_file(&path).map_err(|e| os_refused(&e, &path))?;
    }
    // (the folders emptied)
    let mut places_in = Vec::new();
    walked(staging, &mut places_in);
    places_in.sort_by(|a, b| b.as_os_str().cmp(a.as_os_str()));
    for place in places_in {
        let empty = std::fs::read_dir(&place).is_ok_and(|mut d| d.next().is_none());
        if place != staging && empty {
            std::fs::remove_dir(&place).map_err(|e| os_refused(&e, &place))?;
        }
    }
    let given = name.replace('\\', "/");
    let given = lp_services::paths::Path::new(false, &given)
        .name()
        .to_string();
    let given = match lp_py::lower(&given).ends_with(".zip") {
        true => given.chars().take(given.chars().count() - 4).collect(),
        false => given,
    };
    let candidates = [
        folder.to_string(),
        top,
        given.clone(),
        slug(&given),
        slug(&theme.name),
    ];
    let wanted = candidates
        .into_iter()
        .find(|n| lp_theme::model::valid_theme_name(n))
        .unwrap_or_else(|| "theme".into());
    let _taking = TAKING.lock().unwrap_or_else(|e| e.into_inner());
    let chosen = free_name(&wanted, themes, places);
    let target = themes.join(&chosen);
    std::fs::rename(staging, &target).map_err(|e| os_refused(&e, staging))?;
    Ok(Imported {
        id: chosen,
        name: theme.name,
        files: kept.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_made_of_texts_as_share_py_makes_them() {
        assert_eq!(slug("My Theme!! 2"), "my-theme-2");
        assert_eq!(slug("..."), "");
        assert!(whole_path("C:/x.jpg") && whole_path("a/../b") && !whole_path("a/b.png"));
        assert!(what_it_says("a.webp", b"RIFF\0\0\0\0WEBPVP8"));
        assert!(!what_it_says("a.mp4", b"\0\0\0\x18zzzz"));
    }
}

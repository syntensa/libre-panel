//! What Python's zoneinfo finds by a key ("Asia/Tokyo"), as the app's timezones.zone asks it:
//! a key that is a normalized relative path below the search path (`_validate_tzfile_path`),
//! a file of that name in one of the search path's folders (`find_tzfile`), else in the tzdata
//! package (`load_tzdata`), a TZif file (its magic: what is not one, as zone.tab, is no zone).
//!
//! The search path is PYTHONTZPATH's where that is set, else the one Python is built with:
//! four folders on Linux and macOS, none on Windows, where Python finds its zones in the tzdata
//! package (the app depends on it there). The tzdata package is where the process is told it
//! is (LP_TZDATA: the tests tell lp-check where Python's is); the Rust app brings none yet
//! (docs/RUST.md), so without it no key is a zone on Windows.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Python's TZPATH as it is built (sysconfig's TZPATH; Windows has none).
pub const TZPATH: &[&str] = if cfg!(windows) {
    &[]
} else {
    &[
        "/usr/share/zoneinfo",
        "/usr/lib/zoneinfo",
        "/usr/share/lib/zoneinfo",
        "/etc/zoneinfo",
    ]
};

/// zoneinfo's TZPATH: PYTHONTZPATH's absolute paths where it is set (none where it is empty),
/// else the one Python is built with.
fn search_path() -> &'static [PathBuf] {
    static PATH: OnceLock<Vec<PathBuf>> = OnceLock::new();
    PATH.get_or_init(|| match std::env::var_os("PYTHONTZPATH") {
        Some(given) => std::env::split_paths(&given)
            .filter(|p| p.is_absolute())
            .collect(),
        None => TZPATH.iter().map(PathBuf::from).collect(),
    })
}

/// The tzdata package's folder, where the process is told it is (LP_TZDATA).
fn tzdata() -> Option<&'static Path> {
    static ROOT: OnceLock<Option<PathBuf>> = OnceLock::new();
    ROOT.get_or_init(|| {
        std::env::var_os("LP_TZDATA")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    })
    .as_deref()
}

/// posixpath.normpath of a relative path.
fn normpath(path: &str) -> String {
    let mut comps: Vec<&str> = Vec::new();
    for comp in path.split('/') {
        if comp.is_empty() || comp == "." {
            continue;
        }
        if comp != ".." || comps.is_empty() || comps.last() == Some(&"..") {
            comps.push(comp);
        } else {
            comps.pop();
        }
    }
    if comps.is_empty() {
        ".".into()
    } else {
        comps.join("/")
    }
}

/// _validate_tzfile_path: whether `key` is a normalized relative path that stays below the
/// search path (Python raises ValueError for any other). On Windows by its own paths' rules
/// (ntpath): a backslash parts a path as a slash does, and a drive leads out of the folder.
pub fn valid_key(key: &str) -> bool {
    let key = if cfg!(windows) {
        if key.chars().nth(1) == Some(':') {
            return false; // (C:UTC: a drive's own folder, not below the search path)
        }
        key.replace('\\', "/")
    } else {
        key.to_string()
    };
    if key.starts_with('/') {
        return false; // an absolute path
    }
    let normal = normpath(&key);
    if normal.chars().count() != key.chars().count() {
        return false; // a/../b, a/b/, ./a
    }
    normpath(&format!("_/{normal}")).starts_with("_/")
}

/// find_tzfile: the file of the key in the search path, the first folder that has it.
pub fn find_tzfile(key: &str) -> Option<PathBuf> {
    if !valid_key(key) {
        return None;
    }
    search_path()
        .iter()
        .map(|dir| dir.join(key))
        .find(|path| std::fs::metadata(path).is_ok_and(|m| m.is_file()))
}

/// Whether `dir` holds an entry called exactly `name` that is a folder: a package (or a
/// namespace package) as the import system finds one, its name's case and all.
fn package(dir: &Path, name: &str) -> bool {
    std::fs::read_dir(dir).is_ok_and(|entries| {
        entries
            .flatten()
            .any(|e| e.file_name() == name && e.path().is_dir())
    })
}

/// load_tzdata: the key's file in the tzdata package, as importlib.resources opens it: the
/// key's folders packages below `tzdata.zoneinfo` (each a folder of exactly that name; a dot
/// in one parts it into two), its last part a file in the last of them (opened as the system
/// opens a file: on Windows a name's case does not matter there). (A module of a folder's
/// name, which tzdata has none of, is not looked for.)
pub fn tzdata_file(key: &str) -> Option<PathBuf> {
    let mut dir = tzdata()?.join("zoneinfo");
    let parts: Vec<&str> = key.split('/').collect();
    let (last, folders) = parts.split_last()?;
    for folder in folders {
        for name in folder.split('.') {
            if name.is_empty() || !package(&dir, name) {
                return None;
            }
            dir = dir.join(name);
        }
    }
    let file = dir.join(last);
    std::fs::metadata(&file)
        .is_ok_and(|m| m.is_file())
        .then_some(file)
}

/// timezones.zone(key) is not None: a zone of that name is there to load.
pub fn exists(key: &str) -> bool {
    if key.is_empty() || !valid_key(key) {
        return false;
    }
    let Some(path) = find_tzfile(key).or_else(|| tzdata_file(key)) else {
        return false;
    };
    // (_TZifHeader.from_file: "Invalid TZif file: magic not found" for any other file)
    let mut magic = [0u8; 4];
    std::fs::File::open(path)
        .and_then(|mut f| std::io::Read::read_exact(&mut f, &mut magic))
        .is_ok_and(|()| &magic == b"TZif")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_as_python_checks_them() {
        assert!(valid_key("Asia/Tokyo"));
        assert!(valid_key("UTC"));
        assert!(!valid_key("/etc/localtime"));
        assert!(!valid_key("a/../b"));
        assert!(!valid_key("Europe/"));
        assert!(!valid_key("./UTC"));
        assert!(!valid_key(".."));
        assert!(!valid_key("."));
        assert!(!valid_key("a//b"));
        assert!(valid_key("..a"));
        // (ntpath's on Windows, posixpath's elsewhere)
        assert_eq!(valid_key("..\\zoneinfo\\UTC"), !cfg!(windows));
        assert_eq!(valid_key("C:UTC"), !cfg!(windows));
        assert!(valid_key("Europe\\Berlin"));
    }
}

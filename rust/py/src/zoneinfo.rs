//! What Python's zoneinfo finds by a key ("Asia/Tokyo"), as the app's timezones.zone asks it:
//! a key that is a normalized relative path below the search path (`_validate_tzfile_path`),
//! a file of that name in one of the search path's folders (`find_tzfile`), a TZif file
//! (its magic: what is not one, as zone.tab, is no zone).
//!
//! The search path is the one Python is built with on Linux and macOS. Windows has none:
//! Python finds its zones in the tzdata package there, which the Rust app does not bring yet
//! (docs/RUST.md), so no key is a zone on Windows for now.

use std::path::PathBuf;

/// Python's TZPATH as it is built (sysconfig's TZPATH).
pub const TZPATH: &[&str] = &[
    "/usr/share/zoneinfo",
    "/usr/lib/zoneinfo",
    "/usr/share/lib/zoneinfo",
    "/etc/zoneinfo",
];

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
/// search path (Python raises ValueError for any other).
pub fn valid_key(key: &str) -> bool {
    if key.starts_with('/') {
        return false; // an absolute path
    }
    let normal = normpath(key);
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
    TZPATH
        .iter()
        .map(|dir| PathBuf::from(format!("{dir}/{key}")))
        .find(|path| std::fs::metadata(path).is_ok_and(|m| m.is_file()))
}

/// timezones.zone(key) is not None: a zone of that name is there to load.
pub fn exists(key: &str) -> bool {
    if key.is_empty() || cfg!(windows) {
        return false;
    }
    let Some(path) = find_tzfile(key) else {
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
    }
}

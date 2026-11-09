//! What theme/model.py asks of pathlib and of Python's text files: a path resolved as Python
//! 3.12's Path.resolve() resolves it (posixpath.realpath: links followed where they are, what
//! is not there taken as it is, a loop of links refused; 3.13 lets a loop through), a path
//! joined as `base / rel` joins it, and
//! a file's text as read_text(encoding="utf-8") gives it (UTF-8 decoded strictly, with
//! Python's message for what is not; every line end made "\n").
//!
//! On Windows, Python resolves with the system's own call (ntpath.realpath); here the part of
//! the path that is there is resolved by the system (canonicalize) and the rest taken as it is,
//! which comes to the same for a theme's files.

#[cfg(windows)]
use std::path::Component;
use std::path::{Path, PathBuf};

use crate::model::{Error, Kind, Result};

/// `base / rel` as pathlib joins a relative path: its empty and "." parts dropped.
pub fn join(base: &Path, rel: &str) -> PathBuf {
    let mut out = base.to_path_buf();
    let separators: &[char] = if cfg!(windows) { &['/', '\\'] } else { &['/'] };
    for part in rel.split(separators) {
        if !part.is_empty() && part != "." {
            out.push(part);
        }
    }
    out
}

/// Path.resolve(): the path with its links followed and its "..".
pub fn resolve(path: &Path) -> Result<PathBuf> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|e| Error {
                kind: Kind::Runtime,
                msg: e.to_string(),
            })?
            .join(path)
    };
    resolve_absolute(&absolute)
}

#[cfg(not(windows))]
fn resolve_absolute(path: &Path) -> Result<PathBuf> {
    use std::collections::HashMap;
    let mut seen: HashMap<PathBuf, Option<PathBuf>> = HashMap::new();
    let text = path.to_string_lossy().into_owned();
    match join_real(PathBuf::from("/"), &text, &mut seen) {
        (resolved, true) => Ok(resolved),
        // a loop of links: Path.resolve() refuses it (its stat() of what realpath gave fails
        // with ELOOP)
        (partial, false) => Err(Error {
            kind: Kind::Runtime,
            msg: format!(
                "Symlink loop from {}",
                lp_py::repr(&normpath(&partial).to_string_lossy())
            ),
        }),
    }
}

/// posixpath.normpath of an absolute path: "." and empty parts dropped, ".." taken back, no
/// slash at the end.
#[cfg(not(windows))]
fn normpath(path: &Path) -> PathBuf {
    let mut out = PathBuf::from("/");
    for part in path.to_string_lossy().split('/') {
        match part {
            "" | "." => {}
            ".." => {
                out.pop();
            }
            name => out.push(name),
        }
    }
    out
}

/// posixpath._joinrealpath (not strict): `rest` resolved onto `path`; false where a loop of
/// links stopped it.
#[cfg(not(windows))]
fn join_real(
    mut path: PathBuf,
    rest: &str,
    seen: &mut std::collections::HashMap<PathBuf, Option<PathBuf>>,
) -> (PathBuf, bool) {
    let mut rest = rest.to_string();
    if rest.starts_with('/') {
        rest = rest[1..].to_string();
        path = PathBuf::from("/");
    }
    while !rest.is_empty() {
        let (name, tail) = match rest.split_once('/') {
            Some((name, tail)) => (name.to_string(), tail.to_string()),
            None => (rest.clone(), String::new()),
        };
        rest = tail;
        if name.is_empty() || name == "." {
            continue;
        }
        if name == ".." {
            // (path is absolute here: its parent, the root's own parent the root)
            if let Some(parent) = path.parent() {
                path = parent.to_path_buf();
            }
            continue;
        }
        let newpath = path.join(&name);
        let is_link = std::fs::symlink_metadata(&newpath)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false);
        if !is_link {
            path = newpath;
            continue;
        }
        if let Some(known) = seen.get(&newpath) {
            match known {
                Some(resolved) => {
                    path = resolved.clone();
                    continue;
                }
                None => return (newpath.join(&rest), false),
            }
        }
        seen.insert(newpath.clone(), None);
        let target = std::fs::read_link(&newpath).unwrap_or_default();
        let (resolved, ok) = join_real(path, &target.to_string_lossy(), seen);
        if !ok {
            return (resolved.join(&rest), false);
        }
        seen.insert(newpath, Some(resolved.clone()));
        path = resolved;
    }
    (path, true)
}

#[cfg(windows)]
fn resolve_absolute(path: &Path) -> Result<PathBuf> {
    // the longest part that is there, resolved by the system; the rest as it is
    let mut there = path.to_path_buf();
    let mut rest: Vec<std::ffi::OsString> = Vec::new();
    loop {
        if let Ok(real) = std::fs::canonicalize(&there) {
            let mut out = strip_verbatim(real);
            for part in rest.iter().rev() {
                match Path::new(part).components().next() {
                    Some(Component::ParentDir) => {
                        out.pop();
                    }
                    Some(Component::CurDir) | None => {}
                    _ => out.push(part),
                }
            }
            return Ok(out);
        }
        match (there.file_name().map(|n| n.to_os_string()), there.parent()) {
            (Some(name), Some(parent)) => {
                rest.push(name);
                there = parent.to_path_buf();
            }
            _ => return Ok(path.to_path_buf()),
        }
    }
}

/// `\\?\C:\x` as `C:\x`, as Python writes a resolved path.
#[cfg(windows)]
fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => path,
    }
}

/// read_text(encoding="utf-8"): the file's text, its line ends ("\r\n", "\r") made "\n"; Err:
/// UnicodeDecodeError's message where it is not UTF-8.
pub fn read_text(bytes: &[u8]) -> std::result::Result<String, String> {
    lp_py::codecs::read_text(bytes).map_err(|e| e.to_string())
}

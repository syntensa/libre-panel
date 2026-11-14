//! Where Libre Panel keeps its settings (config.py's config_dir, usual_dir, portable_dir,
//! user_themes_dir and log_file).

use std::path::{Path, PathBuf};

pub const CONFIG_FILENAME: &str = "config.toml";
/// beside the program: a portable copy
pub const PORTABLE_MARK: &str = "portable.txt";
pub const LOG_FILENAME: &str = "libre-panel.log";

/// portable_dir: for a portable copy (on a USB stick, in a folder of its own) the folder
/// `data` beside the program, when a file `portable.txt` is there too; None for an installed
/// one. `program`: the program's file (None: this one).
pub fn portable_dir(program: Option<&Path>) -> Option<PathBuf> {
    let program = match program {
        Some(program) => program.to_path_buf(),
        None => std::env::current_exe().ok()?,
    };
    let program = std::fs::canonicalize(&program).unwrap_or(program);
    let folder = program.parent()?;
    folder
        .join(PORTABLE_MARK)
        .is_file()
        .then(|| folder.join("data"))
}

/// Path.home(): the person's own folder.
fn home() -> PathBuf {
    let name = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    PathBuf::from(std::env::var_os(name).unwrap_or_default())
}

fn set(name: &str) -> Option<PathBuf> {
    std::env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

/// usual_dir: where an installed Libre Panel keeps its settings, the user's own folder.
pub fn usual_dir() -> PathBuf {
    if cfg!(windows) {
        let base = set("APPDATA").unwrap_or_else(|| home().join("AppData").join("Roaming"));
        return base.join("LibrePanel");
    }
    set("XDG_CONFIG_HOME")
        .unwrap_or_else(|| home().join(".config"))
        .join("libre-panel")
}

/// config_dir: the folder of config.toml and the user's themes. LIBRE_PANEL_HOME names
/// another, then a portable copy's `data` folder.
pub fn config_dir() -> PathBuf {
    set("LIBRE_PANEL_HOME")
        .or_else(|| portable_dir(None))
        .unwrap_or_else(usual_dir)
}

pub fn user_themes_dir() -> PathBuf {
    config_dir().join("themes")
}

/// log_file: the log of the app.
pub fn log_file() -> PathBuf {
    config_dir().join("logs").join(LOG_FILENAME)
}

/// copy_tree: `source` into the new folder `target`, without the folders named in `skip`
/// at its top (shutil.copytree with ignore_patterns, for a folder's own names).
fn copy_tree(source: &Path, target: &Path, skip: &[&str]) -> std::io::Result<()> {
    std::fs::create_dir_all(target)?;
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        if skip.iter().any(|skipped| name == *skipped) {
            continue;
        }
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            copy_tree(&path, &target.join(&name), skip)?;
        } else {
            std::fs::copy(&path, target.join(&name))?;
        }
    }
    Ok(())
}

/// take_over_into_portable: on the first start of a portable copy, the settings in the
/// user's own folder (where an installed Libre Panel keeps its) copied into its `data`
/// folder, once; the logs stay behind. The folder they came from, or None; Err: why they
/// were not taken over.
pub fn take_over_into_portable() -> Result<Option<PathBuf>, String> {
    let portable = match set("LIBRE_PANEL_HOME") {
        Some(_) => None,
        None => portable_dir(None),
    };
    let Some(portable) = portable.filter(|portable| !portable.exists()) else {
        return Ok(None);
    };
    let usual = usual_dir();
    if !usual.join(CONFIG_FILENAME).is_file() {
        return Ok(None);
    }
    let mut name = portable.file_name().unwrap_or_default().to_os_string();
    name.push(".part");
    let part = portable.with_file_name(name);
    let _ = std::fs::remove_dir_all(&part);
    let copied =
        copy_tree(&usual, &part, &["logs"]).and_then(|()| std::fs::rename(&part, &portable));
    match copied {
        Ok(()) => Ok(Some(usual)),
        Err(why) => {
            let _ = std::fs::remove_dir_all(&part);
            Err(format!(
                "portable: the settings of {} were not taken over: {why}",
                usual.display()
            ))
        }
    }
}

/// caches the versions before kept and this one does not: the weather symbols' lit bodies
/// (render/light3d.py, until 0.8.0rc1)
const GONE_CACHES: [&str; 1] = ["light3d"];

/// forget_gone_caches: what GONE_CACHES names, out of the settings folder's `cache`.
pub fn forget_gone_caches() {
    for name in GONE_CACHES {
        let folder = config_dir().join("cache").join(name);
        if folder.is_dir() {
            let _ = std::fs::remove_dir_all(folder);
        }
    }
}

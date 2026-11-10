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

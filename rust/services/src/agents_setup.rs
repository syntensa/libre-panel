//! Setting up the agents' hooks and Claude Code's status line, and the AI key
//! (libre_panel/agents.py): what Libre Panel adds to `~/.claude/settings.json` and
//! `~/.codex/hooks.json`, keeping a copy of each file, and takes out again.

use std::io::Write;
use std::path::{Path, PathBuf};

use lp_py::json::Dumps;
use lp_py::{Dict, Value};

use crate::agents::{CODEX_HOOK_EVENTS, HOOK_EVENTS};

pub const KEY_FILE: &str = "ai.json";
/// or `?key=` in the address
pub const HEADER: &str = "X-Libre-Panel-AI";
pub const MAX_BODY: usize = 256 * 1024;
/// what tells Libre Panel's entries from the person's own
pub const MARK: &str = "/api/ai/";

// -- the AI key ------------------------------------------------------------------------------

/// The key in `folder`'s ai.json (the folder of config.toml), "" without one.
pub fn read_key(folder: &Path) -> String {
    let Ok(text) = std::fs::read_to_string(folder.join(KEY_FILE)) else {
        return String::new();
    };
    match lp_py::json::loads(&text) {
        Ok(Value::Dict(data)) => match data.get("key") {
            Some(Value::Str(found)) => found.clone(),
            _ => String::new(),
        },
        _ => String::new(),
    }
}

/// secrets.token_urlsafe: so many random bytes in the URL's letters.
fn token_urlsafe(bytes: usize) -> std::io::Result<String> {
    const LETTERS: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut random = vec![0u8; bytes];
    getrandom::getrandom(&mut random).map_err(|why| std::io::Error::other(why.to_string()))?;
    let mut out = String::new();
    for chunk in random.chunks(3) {
        let bits = chunk
            .iter()
            .fold(0u32, |bits, byte| bits << 8 | u32::from(*byte))
            << (8 * (3 - chunk.len()));
        for n in 0..=chunk.len() {
            out.push(LETTERS[(bits >> (18 - 6 * n) & 63) as usize] as char);
        }
    }
    Ok(out)
}

/// A new key, written to `folder`'s ai.json for its owner alone.
pub fn new_key(folder: &Path) -> std::io::Result<String> {
    let found = token_urlsafe(24)?;
    std::fs::create_dir_all(folder)?;
    let temporary = folder.join(format!(".{KEY_FILE}.{}", std::process::id()));
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut kept = Dict::default();
    kept.insert("key".into(), Value::Str(found.clone()));
    let text = lp_py::json::dumps(&Value::Dict(kept), &Dumps::default());
    options.open(&temporary)?.write_all(text.as_bytes())?;
    std::fs::rename(&temporary, folder.join(KEY_FILE))?;
    Ok(found)
}

pub fn key(folder: &Path, create: bool) -> std::io::Result<String> {
    match read_key(folder) {
        found if found.is_empty() && create => new_key(folder),
        found => Ok(found),
    }
}

// -- the hooks and the status line --------------------------------------------------------------

pub fn curl() -> &'static str {
    if cfg!(windows) {
        "curl.exe"
    } else {
        "curl"
    }
}

pub fn hook_command(port: &str, agent: &str, secret: &str) -> String {
    let url = format!("http://127.0.0.1:{port}{MARK}hook?agent={agent}&key={secret}");
    format!("{} -s -f -m 2 --data-binary \"@-\" \"{url}\"", curl())
}

pub fn status_command(port: &str, secret: &str) -> String {
    let url = format!("http://127.0.0.1:{port}{MARK}status?agent=claude&key={secret}");
    format!("{} -s -f -m 1 --data-binary \"@-\" \"{url}\"", curl())
}

pub fn claude_settings_path(home: &Path) -> PathBuf {
    home.join(".claude").join("settings.json")
}

/// Codex's hooks: in `codex_home` (the environment's CODEX_HOME) where it is set.
pub fn codex_hooks_path(home: &Path, codex_home: Option<&std::ffi::OsStr>) -> PathBuf {
    match codex_home.filter(|base| !base.is_empty()) {
        Some(base) => PathBuf::from(base).join("hooks.json"),
        None => home.join(".codex").join("hooks.json"),
    }
}

/// Whether a table's `command` is one of Libre Panel's.
fn marked(table: &Value) -> bool {
    let Some(table) = table.as_dict() else {
        return false;
    };
    table.get("command").is_some_and(|c| c.str().contains(MARK))
}

fn ours(entry: &Value) -> bool {
    let hooks = entry.as_dict().and_then(|entry| entry.get("hooks"));
    hooks
        .and_then(Value::as_list)
        .is_some_and(|hooks| hooks.iter().any(marked))
}

/// `settings` with Libre Panel's hook (`command`, in the background) on each of `events`; the
/// person's own hooks stay, an earlier one of Libre Panel's is replaced.
pub fn with_hooks(settings: &Dict, events: &[&str], command: &str) -> Result<Dict, String> {
    let mut out = settings.clone();
    let hooks = out
        .entry("hooks".into())
        .or_insert(Value::Dict(Dict::default()));
    let Value::Dict(hooks) = hooks else {
        return Err("\"hooks\" is no table".into());
    };
    for event in events {
        let mut groups = match hooks.get(*event) {
            None => Vec::new(),
            Some(Value::List(groups)) => groups.clone(),
            Some(_) => return Err(format!("\"hooks.{event}\" is no list")),
        };
        groups.retain(|group| !ours(group));
        let mut hook = Dict::default();
        hook.insert("type".into(), Value::Str("command".into()));
        hook.insert("command".into(), Value::Str(command.to_string()));
        hook.insert("async".into(), Value::Bool(true));
        hook.insert("timeout".into(), Value::int(5));
        let mut group = Dict::default();
        group.insert("hooks".into(), Value::List(vec![Value::Dict(hook)]));
        groups.push(Value::Dict(group));
        hooks.insert((*event).to_string(), Value::List(groups));
    }
    Ok(out)
}

/// `settings` without what Libre Panel added (its hooks, its status line).
pub fn without_ours(settings: &Dict) -> Dict {
    let mut out = settings.clone();
    if let Some(Value::Dict(hooks)) = out.get_mut("hooks") {
        let events: Vec<String> = hooks.keys().cloned().collect();
        for event in events {
            let Some(Value::List(groups)) = hooks.get_mut(&event) else {
                continue;
            };
            groups.retain(|group| !ours(group));
            if groups.is_empty() {
                hooks.shift_remove(&event);
            }
        }
        if hooks.is_empty() {
            out.shift_remove("hooks");
        }
    }
    if out.get("statusLine").is_some_and(marked) {
        out.shift_remove("statusLine");
    }
    out
}

/// What of Libre Panel's is in an agent's settings.
#[derive(Clone, Debug, PartialEq)]
pub struct Installed {
    pub hooks: bool,
    pub status_line: bool,
    /// a status line of the person's own
    pub other_status_line: bool,
    /// the port Libre Panel's hooks call (its digits; None: none of them)
    pub port: Option<String>,
}

/// The port in the first address of Libre Panel's in a text.
fn port_in(text: &str) -> Option<String> {
    const HOST: &str = "127.0.0.1:";
    let mut rest = text;
    while let Some(at) = rest.find(HOST) {
        rest = &rest[at + HOST.len()..];
        let digits = rest.len() - rest.trim_start_matches(|c: char| c.is_ascii_digit()).len();
        if digits > 0 && rest[digits..].starts_with(MARK) {
            return Some(rest[..digits].to_string());
        }
    }
    None
}

pub fn installed(settings: &Dict) -> Installed {
    let hooks = settings.get("hooks").and_then(Value::as_dict);
    let found = hooks.is_some_and(|hooks| {
        hooks
            .values()
            .any(|groups| groups.as_list().is_some_and(|g| g.iter().any(ours)))
    });
    let line = settings.get("statusLine");
    let line_ours = line.is_some_and(marked);
    let port = match (found, hooks) {
        (true, Some(hooks)) => port_in(&lp_py::json::dumps(
            &Value::Dict(hooks.clone()),
            &Dumps::default(),
        )),
        _ => None,
    };
    Installed {
        hooks: found,
        status_line: line_ours,
        other_status_line: line.is_some_and(Value::truthy) && !line_ours,
        port,
    }
}

/// An agent's settings file as a table: an empty one where there is no file.
pub fn read_settings(path: &Path) -> Result<Dict, String> {
    let text = match std::fs::read(path) {
        Ok(bytes) => String::from_utf8(bytes).map_err(|why| why.to_string())?,
        Err(why) if why.kind() == std::io::ErrorKind::NotFound => return Ok(Dict::default()),
        Err(why) => return Err(why.to_string()),
    };
    if lp_py::strip(&text).is_empty() {
        return Ok(Dict::default());
    }
    match lp_py::json::loads(&text).map_err(|why| why.to_string())? {
        Value::Dict(data) => Ok(data),
        _ => {
            let name = path.file_name().unwrap_or_default().to_string_lossy();
            Err(format!("{name} holds no settings table"))
        }
    }
}

/// Writes `data` to `path`, keeping the file as it was beside it (`.libre-panel.bak`, once:
/// the first copy is the person's own); the copy, or None for a new file.
pub fn write_settings(path: &Path, data: &Dict) -> std::io::Result<Option<PathBuf>> {
    let folder = path.parent().unwrap_or(Path::new(""));
    std::fs::create_dir_all(folder)?;
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let backup = folder.join(format!("{name}.libre-panel.bak"));
    let mut kept = None;
    if path.exists() {
        if !backup.exists() {
            std::fs::copy(path, &backup)?;
            if let Ok(changed) = std::fs::metadata(path).and_then(|about| about.modified()) {
                let copy = std::fs::OpenOptions::new().write(true).open(&backup)?;
                let _ = copy.set_modified(changed);
            }
        }
        kept = Some(backup);
    }
    let temporary = folder.join(format!(".{name}.{}", std::process::id()));
    let text = lp_py::json::dumps(&Value::Dict(data.clone()), &Dumps::file(2)) + "\n";
    // (written as Python's write_text writes text: on Windows its lines end in \r\n)
    let text = if cfg!(windows) {
        text.replace('\n', "\r\n")
    } else {
        text
    };
    std::fs::write(&temporary, text)?;
    std::fs::rename(&temporary, path)?;
    Ok(kept)
}

/// Claude Code's settings with Libre Panel's hooks, and its status line where wanted and the
/// person has none of their own.
pub fn claude_plan(
    settings: &Dict,
    port: &str,
    secret: &str,
    status_line: bool,
) -> Result<Dict, String> {
    let command = hook_command(port, "claude", secret);
    let mut out = with_hooks(&without_ours(settings), &HOOK_EVENTS, &command)?;
    if status_line && !out.get("statusLine").is_some_and(Value::truthy) {
        let mut line = Dict::default();
        line.insert("type".into(), Value::Str("command".into()));
        line.insert("command".into(), Value::Str(status_command(port, secret)));
        out.insert("statusLine".into(), Value::Dict(line));
    }
    Ok(out)
}

pub fn codex_plan(settings: &Dict, port: &str, secret: &str) -> Result<Dict, String> {
    let command = hook_command(port, "codex", secret);
    with_hooks(&without_ours(settings), &CODEX_HOOK_EVENTS, &command)
}

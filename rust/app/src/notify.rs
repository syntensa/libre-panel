//! Notices from other programs (notify.py): a short message on the panel that a script or a
//! Stream Deck button sends to the running app (`/api/notify`), only where config.toml's
//! `allow_notify` is on and only with the notify key: a secret of its own in `notify.json`
//! beside config.toml, made when it is first needed and readable by this user only.

use std::path::PathBuf;

use lp_py::{Dict, Value};
use lp_render::overlays::Toast;

const KEY_FILE: &str = "notify.json";
/// or `?key=` in the address, for a program that sends no header
pub const HEADER: &str = "X-Libre-Panel-Notify";
/// the kind of these notices, which a theme may switch off (toast.off)
const KIND: &str = "outside";
const MAX_TEXT: usize = 160;
const MAX_TITLE: usize = 40;
const SECONDS: (f64, f64) = (1.0, 60.0);
const DEFAULT_SECONDS: f64 = 5.0;
const LEVELS: [&str; 3] = ["info", "warning", "error"];

fn key_file() -> PathBuf {
    lp_config::dirs::config_dir().join(KEY_FILE)
}

/// read_key: the notify key, or "" where none was made yet.
pub fn read_key() -> String {
    let Ok(text) = std::fs::read_to_string(key_file()) else {
        return String::new();
    };
    match lp_py::json::loads(&text) {
        Ok(Value::Dict(d)) => match d.get("key") {
            Some(Value::Str(key)) => key.clone(),
            _ => String::new(),
        },
        _ => String::new(),
    }
}

/// new_key: a new notify key (the old one stops working), kept for this user only.
pub fn new_key() -> std::io::Result<String> {
    let key = crate::access::token_urlsafe(24)?;
    let path = key_file();
    let folder = path.parent().map(PathBuf::from).unwrap_or_default();
    std::fs::create_dir_all(&folder)?;
    let temporary = folder.join(format!(".{KEY_FILE}.{}", std::process::id()));
    let mut told = Dict::new();
    told.insert("key".into(), Value::Str(key.clone()));
    let text = lp_py::json::dumps(&Value::Dict(told), &lp_py::json::Dumps::default());
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    std::io::Write::write_all(&mut options.open(&temporary)?, text.as_bytes())?;
    std::fs::rename(&temporary, &path)?;
    Ok(key)
}

/// key: the notify key; with `create` one is made where there is none yet.
pub fn key(create: bool) -> std::io::Result<String> {
    let found = read_key();
    match found.is_empty() && create {
        true => new_key(),
        false => Ok(found),
    }
}

/// str.isspace
fn space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

fn text(params: &Dict, name: &str, limit: usize) -> Result<String, String> {
    let value = match params.get(name) {
        None => return Ok(String::new()),
        Some(Value::Str(value)) => value,
        Some(_) => return Err(format!("{name}: a text")),
    };
    // (one line: no line breaks or runs of spaces on the panel)
    let value = value
        .split(space)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if value.chars().count() > limit {
        return Err(format!("{name}: at most {limit} characters"));
    }
    Ok(value)
}

/// float(x); None where Python raises.
fn float_of(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Int(n) => lp_py::num::int_to_float(n).ok(),
        Value::Float(x) => Some(*x),
        Value::Str(s) => lp_py::float_of_str(s),
        _ => None,
    }
}

/// notice: the toast of what a program sent: `text` (needed), `title`, `seconds` (1 to 60, 5
/// when not given), `icon` (one of the icons themes use) and `level`; numbers may come as
/// text, as in an address. Err: what is wrong with it.
pub fn notice(params: &Dict) -> Result<Toast, String> {
    let said = text(params, "text", MAX_TEXT)?;
    if said.is_empty() {
        return Err("text: what the notice says".into());
    }
    let title = text(params, "title", MAX_TITLE)?;
    let seconds = match params.get("seconds") {
        None => DEFAULT_SECONDS,
        Some(value) => float_of(value).ok_or("seconds: a number")?,
    };
    if !(SECONDS.0 <= seconds && seconds <= SECONDS.1) {
        return Err("seconds: from 1 to 60".into());
    }
    let icon = params.get("icon").filter(|icon| icon.truthy());
    let icon = match icon {
        None => None,
        Some(Value::Str(name)) if lp_theme::tables::ICON_NAMES.contains(&name.as_str()) => {
            Some(name.clone())
        }
        Some(_) => {
            return Err(format!(
                "icon: one of {}",
                lp_theme::tables::ICON_NAMES.join(", ")
            ))
        }
    };
    let level = match params.get("level").filter(|level| level.truthy()) {
        None => "info".to_string(),
        Some(Value::Str(level)) if LEVELS.contains(&level.as_str()) => level.clone(),
        Some(_) => return Err("level: info, warning or error".into()),
    };
    let mut payload = Dict::new();
    if !title.is_empty() {
        payload.insert("label".into(), Value::Str(title));
    }
    Ok(Toast {
        icon,
        level,
        seconds: Some(seconds),
        kind: KIND.into(),
        rank: 1,
        payload,
        ..Toast::new(said)
    })
}

/// urllib.parse.quote_plus
fn quote_plus(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b' ' => out.push('+'),
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(byte as char);
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}

/// address: the address a program opens to show `text` (a Stream Deck's "Website" action).
pub fn address(port: u16, key: &str, text: &str) -> String {
    let mut query = format!("key={}", quote_plus(key));
    if !text.is_empty() {
        query.push_str(&format!("&text={}", quote_plus(text)));
    }
    format!("http://127.0.0.1:{port}/api/notify?{query}")
}

/// `GET` and `POST /api/notify-key`: the notify key (made where there is none; `renew`: a new
/// one), the address that shows a notice and the command that does.
pub fn key_state(config: &lp_config::Config, port: u16, renew: bool) -> Result<Value, String> {
    let mut told = Dict::new();
    if !config.allow_notify {
        told.insert("on".into(), Value::Bool(false));
        return Ok(Value::Dict(told));
    }
    let found = match renew {
        true => new_key(),
        false => key(true),
    };
    let key = found.map_err(|why| format!("OSError: {why}"))?;
    let finished = lp_i18n::t("Render finished");
    told.insert("on".into(), Value::Bool(true));
    told.insert("key".into(), Value::Str(key.clone()));
    told.insert("address".into(), Value::Str(address(port, &key, &finished)));
    told.insert(
        "command".into(),
        Value::Str(crate::settings::cli_command(&format!(
            "notify \"{finished}\""
        ))),
    );
    Ok(Value::Dict(told))
}

//! Who may talk to the app's server (editor/access.py).
//!
//! The server listens on 127.0.0.1, which every program of every user of this PC can reach,
//! and it can do a lot: it writes config.toml, and the running Libre Panel starts the
//! programs and loads the libraries that file names. So each run makes a secret token, and a
//! request must carry it in the `X-Libre-Panel` header but for the app's own files.
//!
//! Only the person who started Libre Panel gets the token: a window, the tray and
//! `libre-panel editor` hand it to the app in the address, after a `#` (a browser never
//! sends that part to a server); a program that starts later reads it from a file in the
//! settings folder that only this user can open: mode 600 in a folder of mode 700 on Linux,
//! and on Windows the user's profile, whose ACLs keep the others out.

use std::path::{Path, PathBuf};

use lp_i18n::t;
use lp_py::{Dict, Value};

pub const HEADER: &str = "X-Libre-Panel";
/// in the address' fragment (`#token=...`) and in a picture's query (`?token=`)
pub const NAME: &str = "token";
/// how a program that starts a window tells it the token
#[allow(dead_code)] // (for the programs that start later: the window, `libre-panel quit`)
pub const ENV: &str = "LIBRE_PANEL_TOKEN";
/// in the settings folder: opened by this user only
pub const PRIVATE_DIR: &str = "private";
/// in it: the pid, the port and the token of the running editor
pub const TOKEN_FILE: &str = "editor.json";
/// How many names the temporary file of the token tries: an ACL that keeps the user out of
/// the folder looks like a name in use, and trying without end hung the app at its start.
const ATTEMPTS: usize = 20;

fn say(message: &str) {
    crate::logs::say("libre_panel.editor.access", message);
}

fn token_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

/// _TOKEN: what a token looks like.
fn is_token(text: &str) -> bool {
    (16..=200).contains(&text.len()) && text.bytes().all(token_byte)
}

/// secrets.token_urlsafe: so many random bytes in the characters of an address.
pub fn token_urlsafe(bytes: usize) -> std::io::Result<String> {
    const LETTERS: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut random = vec![0u8; bytes];
    getrandom::getrandom(&mut random).map_err(|why| std::io::Error::other(why.to_string()))?;
    let mut out = String::new();
    for chunk in random.chunks(3) {
        let word = chunk
            .iter()
            .fold(0u32, |word, byte| word << 8 | u32::from(*byte));
        let word = word << (8 * (3 - chunk.len()));
        for n in 0..=chunk.len() {
            out.push(char::from(LETTERS[(word >> (18 - 6 * n) & 63) as usize]));
        }
    }
    Ok(out)
}

/// new_token: a token for one run of the server: 256 bits, in the characters of an address.
pub fn new_token() -> std::io::Result<String> {
    token_urlsafe(32)
}

/// matches: whether a request's token is the right one. The time this takes does not tell
/// how much of it was right.
pub fn matches(given: Option<&str>, expected: &str) -> bool {
    let Some(given) = given.filter(|given| !given.is_empty() && !expected.is_empty()) else {
        return false;
    };
    let (given, expected) = (given.as_bytes(), expected.as_bytes());
    let differs = (given.iter().zip(expected.iter().cycle())).fold(0u8, |d, (a, b)| d | (a ^ b));
    std::hint::black_box(differs) == 0 && given.len() == expected.len()
}

// -- the token in an address ----------------------------------------------------------------------

/// split_token: `url` without the token in its fragment, and the token (None: there is
/// none). The fragment is the app's route and the token, `#/design&token=...` or
/// `#token=...`.
pub fn split_token(url: &str) -> (String, Option<String>) {
    let (head, fragment) = url.split_once('#').unwrap_or((url, ""));
    let (mut token, mut kept) = (None, Vec::new());
    for part in fragment.split('&').filter(|_| !fragment.is_empty()) {
        let (key, value) = part.split_once('=').unwrap_or((part, ""));
        if key == NAME {
            token = token.or_else(|| is_token(value).then(|| value.to_string()));
        } else {
            kept.push(part);
        }
    }
    let rest = kept.join("&");
    match rest.is_empty() {
        true => (head.to_string(), token),
        false => (format!("{head}#{rest}"), token),
    }
}

/// with_token: `url` with `token` in its fragment, after the route if it has one. Without a
/// token it is `url` as it is.
pub fn with_token(url: &str, token: Option<&str>) -> String {
    let Some(token) = token.filter(|token| !token.is_empty()) else {
        return url.to_string();
    };
    let (page, _) = split_token(url);
    let joint = if page.contains('#') { '&' } else { '#' };
    format!("{page}{joint}{NAME}={token}")
}

/// hide_token: `text` (a line of the log) with the tokens in it blanked: this run's token,
/// and the key of the notices from other programs.
#[cfg_attr(not(test), allow(dead_code))] // (for the log of the requests)
pub fn hide_token(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(at) = rest.find(['?', '&', '#']) {
        let after = &rest[at + 1..];
        let name = ["token=", "key="]
            .into_iter()
            .find(|name| after.starts_with(name));
        let secret = name.map(|name| {
            let value = &after[name.len()..];
            (name, value.bytes().take_while(|b| token_byte(*b)).count())
        });
        match secret {
            Some((name, length)) if length > 0 => {
                out.push_str(&rest[..=at]);
                out.push_str(name);
                out.push('…');
                rest = &after[name.len() + length..];
            }
            _ => {
                out.push_str(&rest[..=at]);
                rest = after;
            }
        }
    }
    out + rest
}

// -- the file for the programs that start later ---------------------------------------------------

pub fn token_file(settings: &Path) -> PathBuf {
    settings.join(PRIVATE_DIR).join(TOKEN_FILE)
}

/// _private_dir: make `folder` (and nothing above it), for this user only. An error when it
/// cannot be made so, e.g. it belongs to somebody else.
fn private_dir(folder: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
        if !folder.is_dir() {
            let mut make = std::fs::DirBuilder::new();
            make.recursive(true).mode(0o700).create(folder)?;
        }
        let info = std::fs::metadata(folder)?;
        // SAFETY: geteuid has no arguments and cannot fail.
        if info.uid() != unsafe { libc::geteuid() } {
            let said = format!("{} belongs to another user", folder.display());
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                said,
            ));
        }
        if info.mode() & 0o077 != 0 {
            std::fs::set_permissions(folder, std::fs::Permissions::from_mode(0o700))?;
        }
        Ok(())
    }
    // Windows: what the folder inherits from the user's profile. (Made for its owner alone
    // with administrator rights, its owner is the Administrators group: Libre Panel started
    // without them could not write to it.)
    #[cfg(not(unix))]
    std::fs::create_dir_all(folder)
}

/// _temporary: a new file in `folder` that only this user can open, with a few names at
/// most, and an error at once where the folder may not be written to.
fn temporary(folder: &Path) -> std::io::Result<(std::fs::File, PathBuf)> {
    for _ in 0..ATTEMPTS {
        let mut random = [0u8; 6];
        getrandom::getrandom(&mut random).map_err(|why| std::io::Error::other(why.to_string()))?;
        let name: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
        let path = folder.join(format!(".editor-{name}.tmp"));
        let mut open = std::fs::OpenOptions::new();
        open.read(true).write(true).create_new(true);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut open, 0o600);
        match open.open(&path) {
            Ok(file) => return Ok((file, path)),
            Err(why) if why.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(why) => return Err(why),
        }
    }
    let said = format!("no free name for a temporary file in {}", folder.display());
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, said))
}

/// _write: a temporary file that only this user can open, then a rename: the token is never
/// readable by others, not even for a moment, and a reader never meets half a file.
fn write(path: &Path, info: &Dict) -> std::io::Result<()> {
    use std::io::Write as _;
    let folder = path.parent().unwrap_or(Path::new("."));
    let (mut file, tmp) = temporary(folder)?;
    let text = lp_py::json::dumps(&Value::Dict(info.clone()), &lp_py::json::Dumps::default());
    let written = file.write_all(text.as_bytes()).and_then(|()| {
        drop(file);
        std::fs::rename(&tmp, path)
    });
    if written.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    written
}

/// _set_aside: move a private folder this user may not write to out of the way (Windows),
/// so that a new one can be made: renaming it takes only the right to change the folder
/// above it, which is the user's.
fn set_aside(folder: &Path) -> bool {
    let name = folder.file_name().unwrap_or_default().to_string_lossy();
    for number in 1..100 {
        let aside = folder.with_file_name(format!("{name}-locked-{number}"));
        if aside.exists() {
            continue;
        }
        if std::fs::rename(folder, &aside).is_err() {
            return false;
        }
        say(&format!(
            "could not write to {}: moved it to {} and made it anew",
            folder.display(),
            aside.display()
        ));
        return true;
    }
    false
}

/// publish: write the token of the editor on `port` where the programs of this user find
/// it. None (and a line in the log) when that cannot be done: the app works, but
/// `libre-panel quit` and a window started later cannot sign in.
pub fn publish(settings: &Path, token: &str, port: u16) -> Option<PathBuf> {
    let path = token_file(settings);
    let folder = path.parent()?.to_path_buf();
    let mut info = Dict::new();
    info.insert("pid".into(), Value::int(i64::from(std::process::id())));
    info.insert("port".into(), Value::int(i64::from(port)));
    info.insert("token".into(), Value::Str(token.to_string()));
    let done = private_dir(&folder).and_then(|()| match write(&path, &info) {
        Err(why)
            if why.kind() == std::io::ErrorKind::PermissionDenied
                && cfg!(windows)
                && set_aside(&folder) =>
        {
            private_dir(&folder).and_then(|()| write(&path, &info))
        }
        done => done,
    });
    match done {
        Ok(()) => Some(path),
        Err(why) => {
            let path = path.display();
            say(&format!(
                "the editor's token could not be written to {path}: {why}"
            ));
            None
        }
    }
}

fn read(settings: &Path) -> Option<Dict> {
    let text = std::fs::read_to_string(token_file(settings)).ok()?;
    match lp_py::json::loads(&text).ok()? {
        Value::Dict(info) => Some(info),
        _ => None,
    }
}

/// forget: remove the file if it still holds `token` (a later run may have written its
/// own).
pub fn forget(settings: &Path, token: &str) {
    let held = read(settings).and_then(|info| info.get("token").map(Value::str));
    if held.as_deref() == Some(token) && !token.is_empty() {
        let _ = std::fs::remove_file(token_file(settings));
    }
}

/// inherited_token: the token the program that started this one passed on in its
/// environment (`ENV`). It is taken out of the environment, so that what this program starts
/// does not inherit it.
#[allow(dead_code)] // (for the programs that start later: the window, `libre-panel quit`)
pub fn inherited_token() -> Option<String> {
    let token = std::env::var(ENV).unwrap_or_default();
    std::env::remove_var(ENV);
    is_token(&token).then_some(token)
}

/// pid_exists: whether a program of that number runs.
#[cfg_attr(not(test), allow(dead_code))] // (for the programs that start later: the window, `libre-panel quit`)
pub fn pid_exists(pid: i64) -> bool {
    if pid <= 0 {
        return false;
    }
    #[cfg(unix)]
    {
        let Ok(pid) = libc::pid_t::try_from(pid) else {
            return false;
        };
        // SAFETY: signal 0 only asks whether the process is there.
        let asked = unsafe { libc::kill(pid, 0) };
        // (another user's: it runs)
        asked == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
        use windows_sys::Win32::System::Threading::{
            GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        };
        let Ok(pid) = u32::try_from(pid) else {
            return false;
        };
        // SAFETY: a handle of this call's own, closed before it returns.
        unsafe {
            let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if process.is_null() {
                // (one that may not be opened runs all the same)
                return std::io::Error::last_os_error().raw_os_error() == Some(5);
            }
            let mut code = 0u32;
            let asked = GetExitCodeProcess(process, &mut code);
            CloseHandle(process);
            asked != 0 && code == STILL_ACTIVE as u32
        }
    }
    #[cfg(not(any(unix, windows)))]
    false
}

/// read_token: the token of the running editor, from the file it wrote. With `port`, only
/// when the file is about the server on that port, and when its program still runs.
#[cfg_attr(not(test), allow(dead_code))] // (for the programs that start later: the window, `libre-panel quit`)
pub fn read_token(settings: &Path, port: Option<u16>) -> Option<String> {
    let info = read(settings)?;
    let token = match info.get("token") {
        Some(Value::Str(token)) if is_token(token) => token.clone(),
        _ => return None,
    };
    if let Some(port) = port {
        let whole = |key: &str| match info.get(key) {
            Some(number @ Value::Int(_)) => number.to_f64().map(|n| n as i64),
            _ => None,
        };
        if whole("port") != Some(i64::from(port)) {
            return None;
        }
        // (a run that ended: its token opens nothing)
        if !whole("pid").is_some_and(pid_exists) {
            return None;
        }
    }
    Some(token)
}

// -- rights ---------------------------------------------------------------------------------------

/// elevated: whether this program runs with administrator rights (Windows) or as root
/// (elsewhere).
pub fn elevated() -> bool {
    lp_devices::rights::elevated()
}

/// folder_risk: why a Libre Panel with the highest rights should not read its settings from
/// `folder`, in a few words; "" when nothing is wrong or nothing can be told. It runs with
/// those rights what config.toml and the plugins in it name, so whoever else can write there
/// can run a program as administrator. On Windows the profile of the user (`home`) keeps the
/// others out; elsewhere the owner and the modes of the folder and of what is in it tell.
pub fn folder_risk(folder: &Path, home: Option<&Path>) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let _ = home;
        for path in [
            folder.to_path_buf(),
            folder.join("config.toml"),
            folder.join("plugins"),
        ] {
            let Ok(info) = std::fs::metadata(&path) else {
                continue;
            };
            // SAFETY: geteuid has no arguments and cannot fail.
            if info.uid() != unsafe { libc::geteuid() } {
                return t("it belongs to another user");
            }
            if info.mode() & 0o022 != 0 {
                return t("other users may write to it");
            }
        }
        String::new()
    }
    #[cfg(not(unix))]
    {
        let resolved = |path: &Path| std::fs::canonicalize(path).ok();
        let home = home
            .map(Path::to_path_buf)
            .or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from));
        match (resolved(folder), home.as_deref().and_then(resolved)) {
            (Some(folder), Some(home)) if !folder.starts_with(&home) => {
                t("it is outside your user folder, where other users may be allowed to write")
            }
            _ => String::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_token_is_in_the_address_and_out_of_the_log() {
        let token = new_token().unwrap();
        assert_eq!(token.len(), 43);
        assert!(is_token(&token));
        assert!(matches(Some(&token), &token));
        assert!(!matches(Some(&token[1..]), &token) && !matches(None, &token));
        assert!(!matches(Some(""), "") && !matches(Some(&format!("{token}{token}")), &token));
        let url = "http://127.0.0.1:8765/";
        let with = with_token(url, Some(&token));
        assert_eq!(with, format!("{url}#token={token}"));
        assert_eq!(split_token(&with), (url.to_string(), Some(token.clone())));
        let route = with_token("http://x/#/design", Some(&token));
        assert_eq!(route, format!("http://x/#/design&token={token}"));
        assert_eq!(split_token(&route).0, "http://x/#/design");
        assert_eq!(
            with_token(&route, Some("another-token-of-16"))
                .matches("token=")
                .count(),
            1
        );
        assert_eq!(
            split_token("http://x/#token=short"),
            ("http://x/".into(), None)
        );
        assert_eq!(with_token(url, None), url);
        let line = format!("GET /api/x?token={token}&a=1 and ?key=abc_DEF-1 #token=");
        assert_eq!(
            hide_token(&line),
            "GET /api/x?token=…&a=1 and ?key=… #token="
        );
    }

    #[test]
    fn the_token_file_is_the_users_own() {
        let folder = std::env::temp_dir().join(format!("lp-access-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&folder);
        std::fs::create_dir_all(&folder).unwrap();
        let token = new_token().unwrap();
        let path = publish(&folder, &token, 8765).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            assert_eq!(std::fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
            assert_eq!(
                std::fs::metadata(path.parent().unwrap()).unwrap().mode() & 0o777,
                0o700
            );
        }
        assert_eq!(read_token(&folder, None), Some(token.clone()));
        assert_eq!(read_token(&folder, Some(8765)), Some(token.clone()));
        assert_eq!(read_token(&folder, Some(1)), None);
        forget(&folder, "another");
        assert!(path.is_file());
        forget(&folder, &token);
        assert!(!path.exists() && read_token(&folder, None).is_none());
        assert!(pid_exists(i64::from(std::process::id())) && !pid_exists(0));
        let _ = std::fs::remove_dir_all(&folder);
    }
}

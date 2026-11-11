//! Libre Panel's own window (window.py): the app (start, design, look, live, settings) in a
//! native window, WebView2 on Windows and WebKitGTK on Linux, in a process of its own (the
//! program `libre-panel-window`, rust/desktop), one at a time. This is the side of the window that
//! the app and every start have; the window's program takes it in as well.
//!
//! Whoever wants the app shown (the tray, `libre-panel start`, a second start) calls
//! `open_window`: an open window comes to the front and shows the view asked for; otherwise
//! a window process starts; where none can, the browser opens. A window process holds a lock
//! (`window.lock`) from its first moment to its last: one that starts while another is open or
//! opening never shows a window of its own; it waits for the other to answer and brings that
//! one to the front.
//!
//! The open window answers on a local port, with a token from a file only this user can read
//! (`window.json`): `focus <token> <url>` and `close <token>`. The app's server wants a token of
//! its own: the window brings it to the page in the address, after a `#`; a window process
//! gets it in its environment, or reads it from the file the running app wrote.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::path::Path;
use std::time::{Duration, Instant};

use lp_py::Value;

use crate::access;

pub const VIEWS: [&str; 5] = ["home", "design", "look", "live", "settings"];
/// the open window: pid, port, token
pub(crate) const RUNNING_FILENAME: &str = "window.json";
/// how long a start waits for the open window to answer
pub(crate) const ANSWER_S: f64 = 2.0;
/// next to the tray's log: what the window process says, and why it ended
const WINDOW_LOG: &str = "window.log";
/// a window that ends with an error this soon did not open: the browser instead
const SETTLE_S: f64 = 15.0;
pub(crate) fn say(message: &str) {
    crate::logs::say("libre_panel.window", message);
}

/// view_url: the app's address with a view: `http://127.0.0.1:8765/#/design`.
pub fn view_url(url: &str, view: Option<&str>) -> String {
    let mut base = url.split('#').next().unwrap_or("").to_string();
    if !base.ends_with('/') {
        base.push('/');
    }
    match view {
        Some(view) => format!("{base}#/{view}"),
        None => base,
    }
}

/// The scheme, host and port of an address ("http", "127.0.0.1", Some(8765)).
pub(crate) fn parts(url: &str) -> Option<(&str, &str, Option<u16>)> {
    let (scheme, rest) = url.split_once("://")?;
    let place = rest.split(['/', '?', '#']).next().unwrap_or("");
    let place = place.rsplit('@').next().unwrap_or(place);
    match place.rsplit_once(':') {
        Some((host, port)) if !host.ends_with(']') || port.parse::<u16>().is_ok() => {
            Some((scheme, host, port.parse().ok()))
        }
        _ => Some((scheme, place, None)),
    }
}

pub(crate) fn port_of(url: &str) -> Option<u16> {
    parts(url).and_then(|(_, _, port)| port)
}

/// The window's process as it wrote itself down.
struct Running {
    pid: i64,
    port: u16,
    token: String,
    url: String,
}

/// running: what the open window wrote about itself, if a window is open.
fn running(settings: &Path) -> Option<Running> {
    let text = std::fs::read_to_string(settings.join(RUNNING_FILENAME)).ok()?;
    let Ok(Value::Dict(info)) = lp_py::json::loads(&text) else {
        return None;
    };
    let whole = |key: &str| match info.get(key) {
        Some(Value::Int(n)) => n.to_string().parse::<i64>().ok(),
        _ => None,
    };
    let text = |key: &str| match info.get(key) {
        Some(Value::Str(text)) => text.clone(),
        _ => String::new(),
    };
    let port = u16::try_from(whole("port")?).ok()?;
    let pid = whole("pid").unwrap_or(-1);
    if !access::pid_exists(pid) {
        return None;
    }
    Some(Running {
        pid,
        port,
        token: text("token"),
        url: text("url"),
    })
}

/// _ask: one request to the open window; true when it answered "ok".
fn ask(info: &Running, words: &[&str]) -> bool {
    let mut line = vec![words[0], info.token.as_str()];
    line.extend(&words[1..]);
    let line = line.join(" ") + "\n";
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], info.port));
    let wait = Duration::from_secs_f64(ANSWER_S);
    let Ok(mut link) = TcpStream::connect_timeout(&address, wait) else {
        return false;
    };
    let _ = link.set_read_timeout(Some(wait));
    if link.write_all(line.as_bytes()).is_err() {
        return false;
    }
    let mut answer = String::new();
    let _ = BufReader::new(link).read_line(&mut answer);
    answer.trim() == "ok"
}

/// _address: what a window or a browser opens: `url` on `view`, with the app's token (the one
/// given, the one in `url`, or the one the running app wrote down).
fn address(settings: &Path, url: &str, view: Option<&str>, token: Option<&str>) -> String {
    let (base, inside) = access::split_token(url);
    let token = token
        .map(str::to_string)
        .or(inside)
        .or_else(|| access::read_token(settings, port_of(&base)));
    access::with_token(&view_url(&base, view), token.as_deref())
}

/// Windows lets only the program the user just started bring a window to the front: this start
/// passes that on to the window's process.
fn allow_foreground(pid: i64) {
    #[cfg(windows)]
    if let Ok(pid) = u32::try_from(pid) {
        // SAFETY: a plain call with a process number
        unsafe {
            windows_sys::Win32::UI::WindowsAndMessaging::AllowSetForegroundWindow(pid);
        }
    }
    #[cfg(not(windows))]
    let _ = pid;
}

/// focus_window: the open window brought to the front, showing `view` of `url`; false where
/// none is open.
pub(crate) fn focus_window(
    settings: &Path,
    url: &str,
    view: Option<&str>,
    token: Option<&str>,
) -> bool {
    let Some(info) = running(settings) else {
        return false;
    };
    allow_foreground(info.pid);
    ask(&info, &["focus", &address(settings, url, view, token)])
}

/// close_window: the open window closed (only one that shows `url`'s server, when given).
pub fn close_window(settings: &Path, url: Option<&str>) -> bool {
    let Some(info) = running(settings) else {
        return false;
    };
    if url.is_some_and(|url| view_url(&info.url, None) != view_url(url, None)) {
        return false;
    }
    ask(&info, &["close"])
}

/// webbrowser.open: the address in the system's browser.
pub fn open_in_browser(url: &str) {
    let opened = if cfg!(windows) {
        // (no shell between: an address's & would end the command there)
        std::process::Command::new("rundll32")
            .args(["url.dll,FileProtocolHandler", url])
            .spawn()
    } else {
        std::process::Command::new("xdg-open").arg(url).spawn()
    };
    if let Err(why) = opened {
        say(&format!("the browser did not open: {why}"));
    }
}

/// open_window: the app shown: the open window comes to the front (on `view`), or a new one
/// opens, or with `browser` (or where no window process starts) the browser. "focused",
/// "window" or "browser".
pub fn open_window(
    settings: &Path,
    url: &str,
    view: Option<&str>,
    browser: bool,
    token: Option<&str>,
) -> &'static str {
    let (base, inside) = access::split_token(url);
    let target = view_url(&base, view);
    let token = token
        .map(str::to_string)
        .or(inside)
        .or_else(|| access::read_token(settings, port_of(&base)));
    if !browser && cfg!(any(windows, target_os = "linux")) {
        if focus_window(settings, &base, view, token.as_deref()) {
            return "focused";
        }
        let mut command = match program() {
            Ok(program) => std::process::Command::new(program),
            Err(why) => {
                say(&format!("could not open Libre Panel's window: {why}"));
                open_in_browser(&access::with_token(&target, token.as_deref()));
                return "browser";
            }
        };
        command.arg(&target);
        // (the token goes in the environment: a command line is for all to read)
        if let Some(token) = &token {
            command.env(access::ENV, token);
        }
        detached(&mut command);
        command.stderr(error_output());
        match command.spawn() {
            Ok(process) => {
                watch(process, access::with_token(&target, token.as_deref()));
                return "window";
            }
            Err(why) => say(&format!("could not open Libre Panel's window: {why}")),
        }
    }
    open_in_browser(&access::with_token(&target, token.as_deref()));
    "browser"
}

/// The window's own program, next to this one: GTK and WebKitGTK (Linux) are its alone, so that
/// Libre Panel runs where they are not, and the app's own libraries are not mixed with theirs.
pub fn program() -> std::io::Result<std::path::PathBuf> {
    sibling("libre-panel-window")
}

/// A program of Libre Panel's next to this one (rust/desktop's).
pub fn sibling(name: &str) -> std::io::Result<std::path::PathBuf> {
    let name = format!("{name}{}", std::env::consts::EXE_SUFFIX);
    Ok(std::env::current_exe()?.with_file_name(name))
}

/// The tray icon's program (rust/desktop's libre-panel-tray) for the app at `address` (with
/// its token, which goes in the environment); None where it does not start.
pub fn start_tray(address: &str) -> Option<std::process::Child> {
    let (base, token) = access::split_token(address);
    let mut command = std::process::Command::new(sibling("libre-panel-tray").ok()?);
    command.arg(&base).stdin(std::process::Stdio::null());
    if let Some(token) = token {
        command.env(access::ENV, token);
    }
    match command.spawn() {
        Ok(child) => Some(child),
        Err(why) => {
            say(&format!(
                "no tray icon ({why}); Libre Panel keeps running without one"
            ));
            None
        }
    }
}

/// `libre-panel window URL [--view VIEW]`: the window's program run, its end this one's.
pub fn run_program(url: &str, view: Option<&str>) -> i32 {
    let mut command = match program() {
        Ok(program) => std::process::Command::new(program),
        Err(why) => {
            say(&format!("could not open Libre Panel's window: {why}"));
            return 1;
        }
    };
    command.arg(url);
    if let Some(view) = view {
        command.args(["--view", view]);
    }
    match command.status() {
        Ok(status) => status.code().unwrap_or(1),
        Err(why) => {
            say(&format!("could not open Libre Panel's window: {why}"));
            1
        }
    }
}

/// window_log: where the window process writes what it says (it has no terminal).
pub fn window_log() -> std::path::PathBuf {
    lp_config::dirs::log_file().with_file_name(WINDOW_LOG)
}

/// _error_output: the file the window's error output goes to, so that a failure of WebView2 or
/// of WebKitGTK is not lost; nowhere when it cannot be written. Started anew past a megabyte.
fn error_output() -> std::process::Stdio {
    let path = window_log();
    let opened = (|| -> std::io::Result<std::fs::File> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        if std::fs::metadata(&path).is_ok_and(|m| m.is_file() && m.len() > 1_000_000) {
            std::fs::rename(&path, path.with_file_name(format!("{WINDOW_LOG}.1")))?;
        }
        let mut output = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?;
        let at = crate::logs::asctime();
        let at = at.split(',').next().unwrap_or_default();
        output.write_all(format!("--- {at} the window starts\n").as_bytes())?;
        Ok(output)
    })();
    match opened {
        Ok(output) => output.into(),
        Err(_) => std::process::Stdio::null(),
    }
}

/// _detached: the window process on its own, so that it outlives what started it (a terminal,
/// the tray's restart) and no console window shows.
fn detached(command: &mut std::process::Command) {
    command
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        command.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
}

/// _watch: the app opened in the browser if the window's process ends with an error before it
/// has been open for SETTLE_S: nobody is left without the app.
fn watch(mut process: std::process::Child, address: String) {
    let watched = std::thread::Builder::new()
        .name("window-watch".into())
        .spawn(move || {
            let until = Instant::now() + Duration::from_secs_f64(SETTLE_S);
            loop {
                match process.try_wait() {
                    Ok(Some(status)) if status.success() => return,
                    Ok(Some(status)) => {
                        let code = status.code().map_or("None".into(), |c| c.to_string());
                        let message = format!(
                            "Libre Panel's window ended at once (exit code {code}; {} says \
                                 why): the app opens in the browser instead",
                            window_log().display()
                        );
                        eprintln!("libre-panel: {message}");
                        crate::logs::log(
                            crate::logs::Level::Warning,
                            "libre_panel.window",
                            &message,
                        );
                        open_in_browser(&address);
                        return;
                    }
                    Ok(None) if Instant::now() < until => {
                        std::thread::sleep(Duration::from_millis(100));
                    }
                    // it is open (the process is reaped by no one after this: it lives on)
                    _ => return,
                }
            }
        });
    if let Err(why) = watched {
        say(&format!("could not watch Libre Panel's window: {why}"));
    }
}

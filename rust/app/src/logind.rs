//! Linux: the panel sleeps with the computer and dims while the session is locked
//! (logind.py), as winsession.rs does on Windows.
//!
//! systemd-logind says on the system bus when the computer is about to sleep and when it woke
//! (`PrepareForSleep`), and when a session is locked or unlocked (`Lock`/`Unlock`, and its
//! `LockedHint` as the desktop sets it). `gdbus monitor` (GLib) or `dbus-monitor` prints these
//! signals, one a line; no D-Bus library is needed. A delay lock (`systemd-inhibit
//! --mode=delay`) gives the panel the moment it needs to go dark before the computer sleeps:
//! it is let go once the panel is dark, and taken again after the computer woke.
//!
//! Without logind, or without either program, nothing of this happens, and the panel stays as
//! it is, as before.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

const LOGIN1: &str = "org.freedesktop.login1";
const SESSIONS: &str = "/org/freedesktop/login1/session/";

fn say(message: &str) {
    crate::logs::say("libre_panel.logind", message);
}

/// session_path: the object path of this session on logind's bus (`XDG_SESSION_ID`; "2" is
/// `…/session/_32`, as systemd escapes it); None where it is not known.
pub fn session_path(session_id: &str) -> Option<String> {
    if session_id.is_empty() {
        return None;
    }
    let escaped: String = (session_id.chars().enumerate())
        .map(
            |(i, c)| match c.is_ascii_alphabetic() || (c.is_ascii_digit() && i > 0) {
                true => c.to_string(),
                false => format!("_{:02x}", c as u32),
            },
        )
        .collect();
    Some(format!("{SESSIONS}{escaped}"))
}

/// monitor_command: the program that prints logind's signals, one a line; None without one.
pub fn monitor_command() -> Option<Vec<String>> {
    let found = |name: &str| lp_devices::rights::which(name, &mut Vec::new()).is_some();
    let words = |words: &[&str]| words.iter().map(|w| w.to_string()).collect();
    if found("gdbus") {
        return Some(words(&["gdbus", "monitor", "--system", "--dest", LOGIN1]));
    }
    if found("dbus-monitor") {
        let rule = format!("type='signal',sender='{LOGIN1}'");
        return Some(words(&["dbus-monitor", "--system", &rule]));
    }
    None
}

/// What a line of the monitor's tells.
#[derive(Debug, PartialEq, Eq)]
pub enum Told {
    /// the computer goes to sleep (true) or woke (false)
    Sleep(bool),
    /// this session was locked (true) or unlocked (false)
    Locked(bool),
}

/// The monitor's lines read one by one (gdbus's, or dbus-monitor's, whose arguments come on
/// lines of their own).
pub struct Heard {
    /// this session's path; None: every session's
    session: Option<String>,
    /// dbus-monitor: the signal whose arguments follow ("sleep", or the path of a session whose
    /// properties changed), and whether the next truth is its LockedHint
    pending: Option<String>,
    hint_next: bool,
}

/// `true` or `false` at the start of `text`.
fn truth(text: &str) -> Option<bool> {
    if text.starts_with("true") {
        Some(true)
    } else if text.starts_with("false") {
        Some(false)
    } else {
        None
    }
}

/// A session's path at the start of a gdbus line, before ": " (`[^:\s]+`).
fn session_at_start(line: &str) -> Option<(&str, &str)> {
    let rest = line.strip_prefix(SESSIONS)?;
    let end = rest.find(|c: char| c == ':' || c.is_whitespace())?;
    if end == 0 || !rest[end..].starts_with(": ") {
        return None;
    }
    Some((&line[..SESSIONS.len() + end], &rest[end + 2..]))
}

impl Heard {
    pub fn new(session: Option<String>) -> Heard {
        Heard {
            session,
            pending: None,
            hint_next: false,
        }
    }

    fn ours(&self, path: &str) -> bool {
        self.session
            .as_deref()
            .is_none_or(|session| session == path)
    }

    fn locked(&self, path: &str, locked: bool) -> Option<Told> {
        self.ours(path).then_some(Told::Locked(locked))
    }

    /// heard: one line of the monitor's.
    pub fn heard(&mut self, line: &str) -> Option<Told> {
        // gdbus: "… PrepareForSleep (true,)"
        if let Some(at) = line.find("PrepareForSleep (") {
            let rest = &line[at + "PrepareForSleep (".len()..];
            if let Some(going) =
                truth(rest).filter(|&t| rest[if t { 4 } else { 5 }..].starts_with(",)"))
            {
                return Some(Told::Sleep(going));
            }
        }
        if let Some((path, rest)) = session_at_start(line) {
            // gdbus: "<session>: org.freedesktop.login1.Session.Lock ()"
            let word = rest.split(' ').next().unwrap_or("");
            if rest.contains(' ') && !word.is_empty() {
                for (member, locked) in [("Lock", true), ("Unlock", false)] {
                    if let Some(head) = word.strip_suffix(&format!("Session.{member}")) {
                        if !head.is_empty() {
                            return self.locked(path, locked);
                        }
                    }
                }
            }
            // gdbus: "<session>: … 'LockedHint': <true> …"
            if let Some(at) = rest.find("'LockedHint': <") {
                let after = &rest[at + "'LockedHint': <".len()..];
                if let Some(hint) = truth(after) {
                    if after[if hint { 4 } else { 5 }..].starts_with('>') {
                        return self.locked(path, hint);
                    }
                }
            }
        }
        self.monitor_line(line)
    }

    /// dbus-monitor's lines: the signal, then its arguments.
    fn monitor_line(&mut self, line: &str) -> Option<Told> {
        if let Some((path, interface, member)) = signal(line) {
            (self.pending, self.hint_next) = (None, false);
            if member == "PrepareForSleep" {
                self.pending = Some("sleep".into());
            } else if interface == format!("{LOGIN1}.Session")
                && ["Lock", "Unlock"].contains(&member)
            {
                return self.locked(path, member == "Lock");
            } else if member == "PropertiesChanged" && path.starts_with(SESSIONS) {
                self.pending = Some(path.to_string());
            }
            return None;
        }
        let pending = self.pending.clone()?;
        if line.contains("\"LockedHint\"") {
            self.hint_next = true;
            return None;
        }
        let found = boolean(line)?;
        if pending == "sleep" {
            self.pending = None;
            return Some(Told::Sleep(found));
        }
        if self.hint_next {
            (self.pending, self.hint_next) = (None, false);
            return self.locked(&pending, found);
        }
        None
    }
}

/// `^signal .*path=([^;\s]+); interface=([^;\s]+); member=(\w+)`
fn signal(line: &str) -> Option<(&str, &str, &str)> {
    if !line.starts_with("signal ") {
        return None;
    }
    let field = |text: &'_ str, name: &str| -> Option<(usize, usize)> {
        let key = format!("{name}=");
        let mut from = 0;
        while let Some(at) = text[from..].find(&key) {
            let start = from + at + key.len();
            let end = text[start..]
                .find(|c: char| c == ';' || c.is_whitespace())
                .map_or(text.len(), |e| start + e);
            if end > start && text[end..].starts_with(';') {
                return Some((start, end));
            }
            from = start;
        }
        None
    };
    // (the last "path=" that is followed by "; interface=…; member=…", as the pattern's
    // greedy ".*" takes it)
    let mut found = None;
    let mut from = 0;
    while let Some(at) = line[from..].find("path=") {
        let start = from + at;
        if let Some((ps, pe)) = field(&line[start..], "path") {
            let rest = &line[start + pe..];
            if let Some(rest) = rest.strip_prefix("; interface=") {
                let ie = rest
                    .find(|c: char| c == ';' || c.is_whitespace())
                    .unwrap_or(rest.len());
                if ie > 0 {
                    if let Some(member) = rest[ie..].strip_prefix("; member=") {
                        let me = member
                            .find(|c: char| !(c.is_alphanumeric() || c == '_'))
                            .unwrap_or(member.len());
                        if me > 0 {
                            found =
                                Some((&line[start + ps..start + pe], &rest[..ie], &member[..me]));
                        }
                    }
                }
            }
        }
        from = start + 5;
    }
    found
}

/// `^\s*(?:variant\s+)?boolean (true|false)`
fn boolean(line: &str) -> Option<bool> {
    let mut rest = line.trim_start();
    if let Some(after) = rest.strip_prefix("variant") {
        if after.starts_with(char::is_whitespace) {
            rest = after.trim_start();
        }
    }
    truth(rest.strip_prefix("boolean ")?)
}

/// What the computer's sleep and wake do (the panel's loop stops and starts).
pub type Callback = Box<dyn Fn() + Send + Sync>;

/// LogindWatcher: hears logind's signals in a thread of its own: `on_sleep` before the
/// computer sleeps (the delay lock is let go after it), `on_wake` after it woke; locking and
/// unlocking of this session go to presence.rs.
pub struct Watcher {
    monitor: Mutex<Option<Child>>,
    inhibitor: Arc<Mutex<Option<Child>>>,
}

/// Take the delay lock: the computer waits (up to InhibitDelayMaxSec, 5 s) for the panel
/// before it sleeps.
fn inhibit(inhibitor: &Mutex<Option<Child>>) {
    if lp_devices::rights::which("systemd-inhibit", &mut Vec::new()).is_none() {
        return;
    }
    let Ok(mut held) = inhibitor.lock() else {
        return;
    };
    if held
        .as_mut()
        .is_some_and(|child| matches!(child.try_wait(), Ok(None)))
    {
        return;
    }
    let taken = Command::new("systemd-inhibit")
        .args([
            "--what=sleep",
            "--mode=delay",
            "--who=Libre Panel",
            "--why=The panel goes dark before the computer sleeps",
            "sleep",
            "infinity",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    match taken {
        Ok(child) => *held = Some(child),
        Err(why) => crate::logs::log(
            crate::logs::Level::Debug,
            "libre_panel.logind",
            &format!("no delay lock before sleep: {why}"),
        ),
    }
}

fn let_go(inhibitor: &Mutex<Option<Child>>) {
    if let Ok(mut held) = inhibitor.lock() {
        if let Some(mut child) = held.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Watcher {
    /// start: the monitor started and heard; Err where neither gdbus nor dbus-monitor is there.
    pub fn start(on_sleep: Callback, on_wake: Callback) -> std::io::Result<Watcher> {
        let command = monitor_command()
            .ok_or_else(|| std::io::Error::other("neither gdbus nor dbus-monitor is installed"))?;
        let mut monitor = Command::new(&command[0])
            .args(&command[1..])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let lines = monitor.stdout.take().map(BufReader::new);
        let inhibitor: Arc<Mutex<Option<Child>>> = Arc::default();
        inhibit(&inhibitor);
        let held = Arc::clone(&inhibitor);
        let session = session_path(&std::env::var("XDG_SESSION_ID").unwrap_or_default());
        let thread = std::thread::Builder::new().name("logind".into());
        thread.spawn(move || {
            let mut heard = Heard::new(session);
            for line in lines
                .into_iter()
                .flat_map(BufRead::lines)
                .map_while(Result::ok)
            {
                match heard.heard(&line) {
                    Some(Told::Sleep(true)) => {
                        on_sleep();
                        let_go(&held); // (the panel is dark: the computer may sleep)
                    }
                    Some(Told::Sleep(false)) => {
                        inhibit(&held);
                        on_wake();
                    }
                    Some(Told::Locked(locked)) => {
                        crate::presence::set_away(crate::presence::LOCKED, locked);
                    }
                    None => {}
                }
            }
        })?;
        Ok(Watcher {
            monitor: Mutex::new(Some(monitor)),
            inhibitor,
        })
    }

    pub fn stop(&self) {
        if let Ok(mut monitor) = self.monitor.lock() {
            if let Some(mut child) = monitor.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        let_go(&self.inhibitor);
    }
}

/// _follow_logind: the panel sleeps with the computer and dims while the session is locked;
/// nothing where logind or its monitor is missing.
pub fn follow(on_sleep: Callback, on_wake: Callback) -> Option<Watcher> {
    match Watcher::start(on_sleep, on_wake) {
        Ok(watcher) => Some(watcher),
        Err(why) => {
            say(&format!(
                "the panel does not follow sleep and locking: {why}"
            ));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const OURS: &str = "/org/freedesktop/login1/session/_32";

    #[test]
    fn this_sessions_path_is_escaped_as_systemd_escapes_it() {
        assert_eq!(session_path("2").as_deref(), Some(OURS));
        let c1 = session_path("c1");
        assert_eq!(c1.as_deref(), Some("/org/freedesktop/login1/session/c1"));
        let twelve = session_path("12");
        assert_eq!(
            twelve.as_deref(),
            Some("/org/freedesktop/login1/session/_312")
        );
        assert_eq!(session_path(""), None);
    }

    #[test]
    fn the_computer_going_to_sleep_and_waking_is_heard() {
        let mut heard = Heard::new(Some(OURS.into()));
        let going =
            "/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForSleep (true,)";
        assert_eq!(heard.heard(going), Some(Told::Sleep(true)));
        let woke =
            "/org/freedesktop/login1: org.freedesktop.login1.Manager.PrepareForSleep (false,)";
        assert_eq!(heard.heard(woke), Some(Told::Sleep(false)));
    }

    #[test]
    fn locking_this_session_is_heard_and_another_s_is_not() {
        let mut heard = Heard::new(Some(OURS.into()));
        let other = "/org/freedesktop/login1/session/_33";
        let hint =
            ": org.freedesktop.DBus.Properties.PropertiesChanged ('org.freedesktop.login1.Session',";
        let line = |path: &str, on: &str| format!("{path}{hint} {{'LockedHint': <{on}>}}, @as [])");
        assert_eq!(heard.heard(&line(other, "true")), None);
        assert_eq!(heard.heard(&line(OURS, "true")), Some(Told::Locked(true)));
        assert_eq!(heard.heard(&line(OURS, "false")), Some(Told::Locked(false)));
        let lock = format!("{OURS}: org.freedesktop.login1.Session.Lock ()");
        assert_eq!(heard.heard(&lock), Some(Told::Locked(true)));
        let unlock = format!("{OURS}: org.freedesktop.login1.Session.Unlock ()");
        assert_eq!(heard.heard(&unlock), Some(Told::Locked(false)));
    }

    #[test]
    fn dbus_monitors_lines_are_understood_too() {
        let mut heard = Heard::new(Some(OURS.into()));
        let head = "signal time=1.5 sender=:1.4 -> destination=(null destination) serial=9 path=";
        let sleep = format!(
            "{head}/org/freedesktop/login1; interface=org.freedesktop.login1.Manager; \
             member=PrepareForSleep"
        );
        assert_eq!(heard.heard(&sleep), None);
        assert_eq!(heard.heard("   boolean true"), Some(Told::Sleep(true)));
        let changed = format!(
            "{head}{OURS}; interface=org.freedesktop.DBus.Properties; member=PropertiesChanged"
        );
        let mut told = Vec::new();
        for line in [
            changed.as_str(),
            "   string \"org.freedesktop.login1.Session\"",
            "   array [",
            "      dict entry(",
            "         string \"Active\"",
            "         variant             boolean true",
            "      )",
            "      dict entry(",
            "         string \"LockedHint\"",
            "         variant             boolean true",
            "      )",
        ] {
            told.extend(heard.heard(line));
        }
        assert_eq!(told, vec![Told::Locked(true)]);
        let unlock =
            format!("{head}{OURS}; interface=org.freedesktop.login1.Session; member=Unlock");
        assert_eq!(heard.heard(&unlock), Some(Told::Locked(false)));
    }
}
